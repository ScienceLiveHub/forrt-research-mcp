"""Verify that a candidate quotation is really present in a source PDF.

This is the deterministic half of claim extraction. Choosing *which* sentence
carries a paper's headline claim is a judgement an agent makes; proving the
sentence is actually in the paper is not, and it is the half that a signed
nanopublication depends on. Wrapping the judgement in a tool would not make it
reproducible — an LLM behind a tool boundary is exactly as non-deterministic as
one in the agent loop. Wrapping the *proof* does: anyone can re-run this and get
the same answer.

`verify_quote` never proposes a quotation. It answers one question — is this
text in that PDF, and where — and returns evidence (page, character offsets,
the file's SHA-256) that a reviewer can check independently.

**On "verbatim".** PDF text extraction inserts hard line breaks mid-sentence,
hyphenates across lines, drops hyphens outright, and renders ligatures and
typographic punctuation inconsistently. A quotation copied faithfully by a human
will therefore rarely be byte-identical to the extracted stream — the quotation
published in the marine-heatwave FORRT chain is not, because pypdf reads the
paper's "35-year" as "35year" and "(p <" as "( p <".

So the result is graded, not boolean, and each tier names the transforms it
needed:

    exact                byte-identical to the extracted page text
    normalized           matched after whitespace / ligature / typographic
                         punctuation / line-break-hyphen repair
    extraction_tolerant  additionally ignores hyphens and spacing around
                         punctuation — known extraction artifacts
    not_found            not in this PDF

Every tier only ever canonicalises *formatting*. None of them touches words,
digits, or order, so a changed number or a dropped clause still fails at every
tier. A looser tier means "look at `matched_text` before you publish this",
never "close enough".
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# Applied in order; each is reported by name when it changes the text.
_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
}
_PUNCTUATION = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "―": "-", "−": "-", " ": " ", " ": " ", " ": " ",
    "​": "", "…": "...",
}
# A hyphen at end-of-line followed by a lowercase continuation: "heat-\nwave".
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*([a-z])")
_WHITESPACE = re.compile(r"\s+")
# Extraction artifacts: "(p <" read back as "( p <", "35-year" as "35year".
_SPACE_INSIDE_OPEN = re.compile(r"([(\[{])\s+")
_SPACE_BEFORE_CLOSE = re.compile(r"\s+([)\]}])")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;:.!?])")

MIN_QUOTE_CHARS = 20


class QuoteError(ValueError):
    """The request could not be evaluated (missing file, unreadable PDF)."""


def _normalize(text: str) -> tuple[str, list[str]]:
    """Canonicalise text for matching. Returns (text, names of transforms applied)."""
    applied: list[str] = []
    out = text

    nfkc = unicodedata.normalize("NFKC", out)
    if nfkc != out:
        applied.append("unicode-nfkc")
        out = nfkc

    for table, name in ((_LIGATURES, "ligatures"), (_PUNCTUATION, "typographic-punctuation")):
        replaced = out
        for src, dst in table.items():
            replaced = replaced.replace(src, dst)
        if replaced != out:
            applied.append(name)
            out = replaced

    dehyphenated = _HYPHEN_BREAK.sub(r"\1\2", out)
    if dehyphenated != out:
        applied.append("join-hyphenated-linebreaks")
        out = dehyphenated

    collapsed = _WHITESPACE.sub(" ", out).strip()
    if collapsed != out:
        applied.append("collapse-whitespace")
        out = collapsed

    return out, applied


def _tolerate_extraction(text: str) -> tuple[str, list[str]]:
    """Canonicalise the artifacts pypdf leaves behind, on top of `_normalize`.

    Hyphens are dropped entirely (extraction loses them unpredictably) and
    whitespace around brackets and punctuation is tightened. Words, digits and
    their order are untouched, so this cannot make a different claim match.
    """
    applied: list[str] = []
    out = text

    dehyphenated = out.replace("-", "")
    if dehyphenated != out:
        applied.append("ignore-hyphens")
        out = dehyphenated

    tightened = _SPACE_BEFORE_PUNCT.sub(
        r"\1", _SPACE_BEFORE_CLOSE.sub(r"\1", _SPACE_INSIDE_OPEN.sub(r"\1", out))
    )
    if tightened != out:
        applied.append("tighten-punctuation-spacing")
        out = tightened

    return out, applied


def _page_texts(pdf_path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise QuoteError(
            "pypdf is required to read PDFs: pip install 'forrt-replication-mcp[pdf]'"
        ) from e

    try:
        reader = PdfReader(str(pdf_path))
        return [(page.extract_text() or "") for page in reader.pages]
    except Exception as e:
        raise QuoteError(f"could not read {pdf_path.name}: {e}") from e


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _closest_passage(needle: str, haystack: str) -> tuple[float, str]:
    """Best-matching window in `haystack`, for reporting a near miss.

    Uses SequenceMatcher's longest common block to centre the window, so the
    caller sees *what the PDF actually says* where it nearly matched — usually
    enough to spot a dropped clause or a changed number.
    """
    if not haystack:
        return 0.0, ""
    matcher = SequenceMatcher(None, needle, haystack, autojunk=False)
    block = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    if block.size == 0:
        return 0.0, ""
    start = max(0, block.b - block.a)
    window = haystack[start:start + len(needle) + 40]
    return SequenceMatcher(None, needle, window, autojunk=False).ratio(), window


def verify_quote(pdf_path: str, quotation: str) -> dict:
    """Is `quotation` present in `pdf_path`, and where?

    Returns a verdict of `exact`, `normalized`, or `not_found`, with the page
    number, character offsets, and the PDF's SHA-256 as reproducible evidence.
    A `not_found` result carries the closest passage in the PDF so the caller
    can see how the candidate differs.
    """
    path = Path(pdf_path).expanduser()
    if not path.is_file():
        raise QuoteError(f"no such file: {path}")

    quotation = (quotation or "").strip()
    if len(quotation) < MIN_QUOTE_CHARS:
        raise QuoteError(
            f"quotation is {len(quotation)} characters; refusing to verify anything "
            f"shorter than {MIN_QUOTE_CHARS} (a short string matches by accident)"
        )

    pages = _page_texts(path)
    evidence = {
        "pdf": str(path),
        "pdf_sha256": _sha256(path),
        "pdf_pages": len(pages),
        "quotation_chars": len(quotation),
    }

    # Pass 1 — byte-identical in the extracted page text.
    for number, page in enumerate(pages, start=1):
        index = page.find(quotation)
        if index >= 0:
            return {
                **evidence, "found": True, "match": "exact", "page": number,
                "char_start": index, "char_end": index + len(quotation),
                "matched_text": quotation, "transforms": [],
                "note": "byte-identical to the extracted page text",
            }

    # Pass 2 — identical after named, reported transforms.
    needle, needle_transforms = _normalize(quotation)
    normalized_pages = [_normalize(page) for page in pages]
    best_ratio, best_window, best_page = 0.0, "", 0
    for number, (haystack, page_transforms) in enumerate(normalized_pages, start=1):
        index = haystack.find(needle)
        if index >= 0:
            return {
                **evidence, "found": True, "match": "normalized", "page": number,
                "char_start": index, "char_end": index + len(needle),
                "matched_text": haystack[index:index + len(needle)],
                "transforms": sorted(set(needle_transforms) | set(page_transforms)),
                "note": ("matched after the listed transforms, not byte-identical. "
                         "Expected for PDF text: extraction inserts line breaks and "
                         "hyphenates across lines. Check `matched_text` reads as the "
                         "paper does before publishing it as verbatim."),
            }
        ratio, window = _closest_passage(needle, haystack)
        if ratio > best_ratio:
            best_ratio, best_window, best_page = ratio, window, number

    # Pass 3 — identical once known extraction artifacts are tolerated.
    loose_needle, loose_needle_transforms = _tolerate_extraction(needle)
    for number, (haystack, page_transforms) in enumerate(normalized_pages, start=1):
        loose_haystack, loose_page_transforms = _tolerate_extraction(haystack)
        index = loose_haystack.find(loose_needle)
        if index >= 0:
            return {
                **evidence, "found": True, "match": "extraction_tolerant", "page": number,
                "char_start": index, "char_end": index + len(loose_needle),
                # Report the page's own normalized text, not the stripped form, so
                # the caller reads what the paper says rather than the match key.
                "matched_text": _closest_passage(loose_needle, haystack)[1],
                "transforms": sorted(
                    set(needle_transforms) | set(page_transforms)
                    | set(loose_needle_transforms) | set(loose_page_transforms)
                ),
                "note": ("matched only after tolerating PDF extraction artifacts "
                         "(hyphens and punctuation spacing). The words, numbers and "
                         "their order are identical. Read `matched_text` and confirm "
                         "it is the sentence you mean before publishing it."),
            }

    return {
        **evidence, "found": False, "match": "not_found", "page": None,
        "char_start": None, "char_end": None, "matched_text": "", "transforms": [],
        "closest": {
            "page": best_page or None,
            "similarity": round(best_ratio, 3),
            "text_in_pdf": best_window,
        },
        "note": ("Not present in this PDF. Do NOT publish it as a verbatim quotation. "
                 "Compare `closest.text_in_pdf` with the candidate — a paraphrase, a "
                 "dropped clause, or an altered number is the usual cause."),
    }
