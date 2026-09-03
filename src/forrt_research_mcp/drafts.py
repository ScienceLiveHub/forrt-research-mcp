"""Check a drafted nanopub against the template it will be published into.

This runs the pre-flight checklist that `docs/forrt-form-fields.md` asks a
drafter to run, except it actually runs it: every field id checked against the
live template, every choice value against the template's own enumeration, every
DOI against the registry, every length cap against the template's regex.

It is the composition the other tools exist for. Individually they answer "is
this value real?"; together they answer the question that matters before
publishing — "is this draft publishable, and if not, exactly where is it wrong?"

Findings carry a severity, and the distinction is deliberate:

    error    would publish something false or be rejected by the form
    warning  a human should look, but it may be intentional
    info     checked and fine, or deliberately not checked

Nothing here guesses. Where a draft convention puts a value somewhere this
cannot read — the CiTO template's citation table, for instance — it says the
field was not checked rather than reporting it as absent.
"""
from __future__ import annotations

import re
from pathlib import Path

from .api import ApiError
from .grounding import GroundingError, resolve_doi, wikidata_lookup
from .templates import VOCABULARIES, template_fields, vocabulary

# `<!-- field: id -->` through to the next marker (or end of file).
_FIELD_RE = re.compile(
    r"<!--\s*field:\s*([A-Za-z0-9_]+)\s*-->(.*?)(?=<!--\s*field:|\Z)", re.S)
# Free text goes in a fenced block; a restricted choice is a ticked checkbox.
_FENCE_RE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
_TICKED_RE = re.compile(r"^\s*[-*]\s*\[x\]\s*(.+?)\s*$", re.M | re.I)
_UNTICKED_RE = re.compile(r"^\s*[-*]\s*\[\s*\]\s*(.+?)\s*$", re.M)

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s)\]\"'<>,;`*]+")
_DOI_TRAILING = ".,;:`*)]}>"
_QID_RE = re.compile(r"\b(Q\d{2,})\b")
# Three kinds of placeholder appear in real drafts, and only one is a problem.
#   back-reference  «URI of step 05 (FORRT Replication Outcome)»
#                   the chain wizard carries the previous step's published URI in
#   release token   {{ZENODO_VERSION_DOI}}
#                   substituted at release by .github/workflows/release-identifiers.yml
#   unfilled        anything else still standing in for a value
_BACKREF_RE = re.compile(r"«[^»]*\bstep\s*\d+[^»]*»", re.I)
_RELEASE_TOKEN_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
_PLACEHOLDER_RE = re.compile(r"«[^»]*»|\{\{[^}]*\}\}|<[A-Z_]{3,}>|_not yet published_")

# draft filename stem -> chain step in the template registry.
STEP_BY_FILE = {
    "01_quote": "01_quote", "01_pico": "01_pico", "01_pcc": "01_pcc",
    "02_aida": "02_aida", "03_claim": "03_claim", "04_study": "04_study",
    "05_outcome": "05_outcome", "06_citation": "06_citation",
    "07_research_software": "07_research_software", "08_synthesis": "08_synthesis",
}


def _finding(severity: str, check: str, message: str, **extra) -> dict:
    return {"severity": severity, "check": check, "message": message, **extra}


def parse_draft(text: str, spec: dict | None = None) -> dict[str, dict]:
    """Field id -> {value, source}.

    Real drafts use two conventions: a fenced block for free text, and a ticked
    `- [x]` checkbox for a restricted choice. Which to read is decided by the
    field's kind on the template, not by which appears first — a `<!-- field -->`
    marker runs to the next marker, so it can enclose an unrelated checkbox
    (the Quote template's DOI field encloses the whole-text/start-end radio),
    and a choice field often carries a fenced block of rationale as well.
    """
    kinds = {f["id"]: f.get("kind", "") for f in (spec or {}).get("fields", [])}

    fields: dict[str, dict] = {}
    for fid, body in _FIELD_RE.findall(text):
        prefers_choice = kinds.get(fid) in ("restricted_choice", "guided_choice")
        ticked = _TICKED_RE.search(body) if prefers_choice or fid not in kinds else None
        if ticked:
            fields[fid] = {"value": ticked.group(1).strip(), "source": "checkbox"}
            continue
        fenced = _FENCE_RE.search(body)
        fields[fid] = {
            "value": fenced.group(1).strip() if fenced else "",
            "source": "fenced-block" if fenced else "none",
        }
    return fields


def _step_for(path: Path, step: str = "") -> str:
    if step:
        return step
    stem = path.stem
    if stem in STEP_BY_FILE:
        return STEP_BY_FILE[stem]
    raise ApiError(
        f"cannot tell which chain step {path.name!r} is. Pass `step` explicitly; "
        f"known: {', '.join(sorted(set(STEP_BY_FILE.values())))}"
    )


def _is_backreference(field: dict) -> bool:
    """Does the chain wizard fill this field itself?

    A `guided_choice` field selects an already-published nanopub by searching
    the network (`possibleValuesFromApi`), and the wizard carries each published
    URI into the next step's back-reference automatically. A drafted placeholder
    there is therefore correct, not an unfilled field.
    """
    return field.get("kind") == "guided_choice" and bool(field.get("values_from_api"))


def _check_fields(fields: dict, spec: dict) -> list[dict]:
    """Field ids exist, required ones are filled, values respect constraints."""
    out: list[dict] = []
    by_id = {f["id"]: f for f in spec["fields"]}

    for fid in sorted(set(fields) - set(by_id)):
        out.append(_finding(
            "error", "field-exists",
            f"{fid!r} is not a field on this template — it will be dropped or "
            f"rejected. Real fields: {', '.join(by_id)}",
            field=fid))

    # A draft where most required content is still blank is an unstarted
    # skeleton, not a broken draft. Say that once instead of once per field.
    substantive = [f for fid, f in by_id.items()
                   if f.get("required") and not _is_backreference(f)]
    blank = [f for f in substantive if not fields.get(f["id"], {}).get("value")]
    skeleton = len(substantive) >= 3 and len(blank) >= max(3, len(substantive) * 0.6)
    if skeleton:
        out.append(_finding(
            "warning", "skeleton",
            f"this looks like an unfilled skeleton: {len(blank)} of "
            f"{len(substantive)} substantive required fields are empty. Nothing "
            f"is wrong with it — it has not been drafted yet",
            fields=[f["id"] for f in blank]))

    for fid, meta in fields.items():
        field = by_id.get(fid)
        if field is None:
            continue
        value = meta["value"]

        if not value:
            if skeleton or _is_backreference(field):
                continue  # already reported once, or filled at publish time
            severity = "error" if field.get("required") else "info"
            out.append(_finding(
                severity, "required",
                f"{fid!r} is {'required but ' if field.get('required') else ''}"
                f"empty in the draft",
                field=fid))
            continue

        if _BACKREF_RE.search(value) or (_PLACEHOLDER_RE.search(value)
                                         and _is_backreference(field)):
            out.append(_finding(
                "info", "backreference",
                f"{fid!r} is a placeholder, which is correct here — the chain "
                f"wizard fills it from the previously published step",
                field=fid))
            continue

        if _RELEASE_TOKEN_RE.search(value):
            out.append(_finding(
                "warning", "release-token",
                f"{fid!r} holds a release-time token ({value[:60]}), substituted "
                f"by the release workflow. Fine while drafting — confirm the "
                f"release has run and the token is resolved before publishing",
                field=fid, value=value[:120]))
            continue

        if _PLACEHOLDER_RE.search(value):
            out.append(_finding(
                "error", "placeholder",
                f"{fid!r} still contains a placeholder — publishing it would "
                f"sign a literal template token",
                field=fid, value=value[:120]))
            continue

        # The template's regex is where length caps actually live.
        regex = field.get("regex")
        if regex:
            try:
                if not re.fullmatch(regex, value, re.S):
                    out.append(_finding(
                        "error", "constraint",
                        f"{fid!r} does not satisfy the template's constraint "
                        f"(regex {regex!r}); {len(value)} characters",
                        field=fid, length=len(value), regex=regex))
            except re.error:
                out.append(_finding(
                    "info", "constraint",
                    f"{fid!r} has a template regex this checker could not compile "
                    f"({regex!r}); not checked",
                    field=fid))

    missing = [fid for fid, f in by_id.items()
               if f.get("required") and fid not in fields]
    if missing and not skeleton:
        out.append(_finding(
            "warning", "coverage",
            f"required field(s) not marked in the draft: {', '.join(missing)}. "
            f"They may be filled in prose or a table this checker cannot read — "
            f"confirm before publishing",
            fields=missing))
    return out


def _check_vocabularies(fields: dict, step: str, *, live: bool) -> list[dict]:
    """Every choice value must be one the form will actually accept."""
    out: list[dict] = []
    for name, (vocab_step, field_id) in VOCABULARIES.items():
        if vocab_step != step or field_id not in fields:
            continue
        value = fields[field_id]["value"]
        if not value:
            continue

        terms = vocabulary(name, live=live)
        allowed = terms.get("values") or []
        if not allowed:
            out.append(_finding(
                "info", "vocabulary",
                f"{field_id!r} not checked: {name} could not be resolved "
                f"({terms.get('source')})",
                field=field_id, vocabulary=name))
            continue

        probe = value.split("\n")[0].strip().strip("`*_ ").lower()
        match = next((a for a in allowed
                      if probe == (a["label"] or "").lower()
                      or (a["label"] or "").lower().startswith(probe)
                      or probe in a["uri"].lower()), None)
        if match:
            out.append(_finding(
                "info", "vocabulary",
                f"{field_id!r} = {match['uri'].rsplit('/', 1)[-1]}",
                field=field_id, vocabulary=name, uri=match["uri"]))
        else:
            out.append(_finding(
                "error", "vocabulary",
                f"{field_id!r} = {probe!r} is not one of the {len(allowed)} values "
                f"this field accepts. Allowed: "
                f"{', '.join((a['label'] or a['uri']).split(' -')[0] for a in allowed)}",
                field=field_id, vocabulary=name, value=probe))
    return out


def _check_identifiers(text: str) -> list[dict]:
    """Every DOI resolves; every Wikidata QID exists."""
    out: list[dict] = []

    dois = set()
    for m in _DOI_RE.finditer(text):
        # `10.5281/zenodo.<N>` in prose is a documented placeholder, not a DOI.
        if text[m.end():m.end() + 1] == "<":
            continue
        dois.add(m.group(0).rstrip(_DOI_TRAILING))

    for doi in sorted(dois):
        try:
            result = resolve_doi(doi)
        except GroundingError as e:
            out.append(_finding("error", "doi", f"{doi} is malformed: {e}", doi=doi))
            continue
        if result["resolves"]:
            out.append(_finding(
                "info", "doi", f"{doi} -> {result['title'][:70]}",
                doi=doi, title=result["title"], year=result["year"]))
        elif result["status"] == 404:
            out.append(_finding(
                "error", "doi",
                f"{doi} is NOT REGISTERED — do not publish it. A well-formed DOI "
                f"is not a real one",
                doi=doi))
        else:
            out.append(_finding(
                "warning", "doi",
                f"{doi} could not be confirmed (HTTP {result['status']}); this may "
                f"be transient — retry before concluding the DOI is wrong",
                doi=doi))

    for qid in sorted(set(_QID_RE.findall(text))):
        try:
            found = wikidata_lookup(qid, limit=1)
        except GroundingError:
            continue
        if any(c["qid"] == qid for c in found["candidates"]):
            hit = next(c for c in found["candidates"] if c["qid"] == qid)
            out.append(_finding(
                "info", "wikidata", f"{qid} -> {hit['label']} ({hit['description']})",
                qid=qid))
        else:
            out.append(_finding(
                "warning", "wikidata",
                f"{qid} did not come back from a search for its own id — confirm "
                f"it exists before publishing",
                qid=qid))
    return out


def validate_draft(path: str, step: str = "", *, live: bool = True) -> dict:
    """Check one drafted nanopub against its template and the real world."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise ApiError(f"no such draft: {target}")

    resolved = _step_for(target, step)
    text = target.read_text()
    spec = template_fields(resolved, live=live)
    fields = parse_draft(text, spec)

    if not fields:
        return {
            "draft": str(target), "step": resolved, "publishable": False,
            "findings": [_finding(
                "error", "parse",
                "no `<!-- field: … -->` markers found — this checker reads the "
                "draft skeletons produced by the template; a free-form file "
                "cannot be checked")],
            "counts": {"error": 1, "warning": 0, "info": 0},
        }

    findings = (_check_fields(fields, spec)
                + _check_vocabularies(fields, resolved, live=live)
                + _check_identifiers(text))

    counts = {level: sum(1 for f in findings if f["severity"] == level)
              for level in ("error", "warning", "info")}
    return {
        "draft": str(target),
        "step": resolved,
        "templateUri": spec["templateUri"],
        "templateSource": spec["source"],
        "fieldsDrafted": len(fields),
        "publishable": counts["error"] == 0,
        "counts": counts,
        "findings": sorted(findings,
                           key=lambda f: ("error", "warning", "info").index(f["severity"])),
    }


def validate_drafts(directory: str, *, live: bool = True) -> dict:
    """Check every recognisable draft in a `nanopubs/drafts/` directory."""
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise ApiError(f"not a directory: {folder}")

    drafts = sorted(p for p in folder.glob("*.md") if p.stem in STEP_BY_FILE)
    if not drafts:
        raise ApiError(
            f"no recognisable drafts in {folder} (expected files named like "
            f"{', '.join(sorted(STEP_BY_FILE)[:3])}…)")

    results = [validate_draft(str(p), live=live) for p in drafts]
    totals = {level: sum(r["counts"][level] for r in results)
              for level in ("error", "warning", "info")}
    return {
        "directory": str(folder),
        "draftsChecked": len(results),
        "publishable": totals["error"] == 0,
        "counts": totals,
        "drafts": results,
    }
