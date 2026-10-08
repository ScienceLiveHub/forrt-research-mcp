"""Verify a published FORRT chain against the network and against itself.

The final pre-announcement check: every URI in `nanopubs/PUBLISHED.md` really
is published, the chain hangs together, the Outcome points at this repository,
the cited DOIs resolve, and the CiTO relation agrees with the Outcome's verdict.

Read-only. It never edits, retracts or supersedes anything; a failing row is
something for a human to act on.

**A constellation alone cannot do this.** The walk routinely stops short of the
upstream anchors: on two real chains it enumerated `[Claim, Study, Outcome]` and
`[Study, Outcome, CiTO]` while the Quote, AIDA and Claim nanopubs listed in
those repos' ledgers were published and merely unreachable. Verifying from the
constellation alone would fail both. So anything the constellation does not
enumerate is checked by fetching its TriG directly — and against the bare
`w3id.org/np/` resolver, because the `/sciencelive/np/` form serves an HTML
viewer that answers 200 and would pass a status-only check while serving no
nanopub at all.
"""
from __future__ import annotations

import re
from pathlib import Path

from .api import ApiError, canonical_uri, fetch_trig
from .constellation import summary as _summary
from .grounding import GroundingError, resolve_doi

# `| 05 | FORRT Replication Outcome | https://w3id.org/…/RA… | 2026-08-15 |`
# The URI cell, not the whole row's third field: a ledger may write the URI
# bare, in backticks, or as a markdown link. `\S+` matched only the first
# whitespace-free token, so `[`RA…`](https://…)` failed to parse and a fully
# published chain was reported as having no URIs at all.
_ROW_RE = re.compile(
    r"^\|\s*(\d{2})\s*\|([^|]*)\|([^|]*)\|", re.M)
_URI_RE = re.compile(r"^https://w3id\.org/(?:sciencelive/)?np/RA[A-Za-z0-9_-]{20,}$")
_ANY_URI_RE = re.compile(r"https://w3id\.org/(?:sciencelive/)?np/RA[A-Za-z0-9_-]{20,}")

STEP_NAMES = {
    "01": "Quote / PICO / PCC", "02": "AIDA Sentence", "03": "FORRT Claim",
    "04": "FORRT Replication Study", "05": "FORRT Replication Outcome",
    "06": "CiTO Citation", "07": "Research Software", "08": "Research Synthesis",
}
# Steps a chain must publish. A CiTO (06) asserts a relation to an *existing*
# work, so a study starting from scratch has nothing to cite and legitimately
# stops at the Outcome. Every other step is required in all three modes.
REQUIRED_STEPS = ("01", "02", "03", "04", "05", "06")
REQUIRED_STEPS_NEW_RESEARCH = ("01", "02", "03", "04", "05")
MODES = ("auto", "replication", "reproduction", "new_research")
# Steps the constellation walk enumerates as chain steps; the rest are checked
# by direct TriG fetch.
ENUMERATED = {"04": "Study", "05": "Outcome", "06": "CiTO", "03": "Claim",
              "02": "AIDA", "01": "Quote"}

# The CiTO relation implied by an Outcome verdict. Mirrors RELATION_FROM_STATUS
# in the template's scripts/build_chain_draft.py, which is what fills the field.
CITO = "http://purl.org/spar/cito/"
RELATION_FROM_VERDICT = {
    "validated": CITO + "confirms",
    "partiallysupported": CITO + "qualifies",
    "contradicted": CITO + "disputes",
    "inconclusive": CITO + "discusses",
    "nottested": CITO + "cites",
}


def _row(status: str, check: str, message: str, **extra) -> dict:
    return {"status": status, "check": check, "message": message, **extra}


def parse_published(text: str) -> dict[str, str]:
    """Step number -> published URI, skipping rows with no URI yet.

    Liberal in what it accepts from the URI cell. Ledgers are written by hand
    and the URI turns up bare, in backticks, or as a markdown link:

        | 05 | Outcome | https://w3id.org/sciencelive/np/RA...        |
        | 05 | Outcome | `https://w3id.org/sciencelive/np/RA...`      |
        | 05 | Outcome | [`RACgxEz...`](https://w3id.org/.../RA...)   |

    All three name the same nanopub. An earlier version required the cell to BE
    the URI, so the markdown-link form parsed as zero URIs and `verify_chain`
    reported "no published URIs found" for a chain that was fully published --
    the second time a ledger layout produced a wrong verdict rather than an
    honest "I cannot read this" (see `unparsed_uris`).
    """
    found: dict[str, str] = {}
    for step, _template, cell in _ROW_RE.findall(text):
        match = _ANY_URI_RE.search(cell)
        if match:
            found[step] = match.group(0)
    return found


def unparsed_uris(text: str, parsed: dict[str, str]) -> list[str]:
    """Nanopub URIs in the ledger that the step table did not account for.

    Ledgers vary. `pangeo-fish-replication` lists step 07 in the numbered table
    and the rest in a second table keyed by description ("PCC question: …",
    "FORRT Replication Study: …"), so a step-number parser sees one row and
    would report the chain as incomplete — a false failure about a chain that is
    published. Counting the URIs the parser did NOT claim turns that from a
    wrong verdict into an explicit "this ledger uses a layout I cannot read".
    """
    claimed = {canonical_uri(u) for u in parsed.values()}
    seen: list[str] = []
    for match in _ANY_URI_RE.finditer(text):
        uri = match.group(0)
        if canonical_uri(uri) not in claimed and uri not in seen:
            seen.append(uri)
    return seen


def _entry_uri(published: dict[str, str]) -> str:
    """Deepest published URI — the walk is bidirectional, so any of these
    surfaces the same constellation, but the apex reaches most of it."""
    for step in ("08", "06", "05"):
        if step in published:
            return published[step]
    return next(iter(published.values()))


def _check_reachable(step: str, uri: str, in_constellation: set[str]) -> dict:
    """Present in the constellation, or served as RDF by the resolver."""
    if canonical_uri(uri) in in_constellation:
        return _row("pass", "reachable",
                    f"step {step} is enumerated in the constellation",
                    step=step, uri=uri)
    try:
        fetch_trig(uri)
    except ApiError as e:
        return _row("fail", "reachable",
                    f"step {step} is neither in the constellation nor served by "
                    f"the resolver: {e}",
                    step=step, uri=uri)
    return _row("pass", "reachable",
                f"step {step} is not enumerated by the walk (normal for upstream "
                f"anchors) but its TriG resolves — published",
                step=step, uri=uri)


def _check_repository(view: dict, published: dict, repo_url: str) -> list[dict]:
    outcome = None
    for chain in view["chains"]:
        for chain_step in chain["steps"]:
            if chain_step["step"] == "Outcome" and (
                    canonical_uri(chain_step["uri"]) == canonical_uri(published.get("05", ""))):
                outcome = chain_step
    if outcome is None:
        return [_row("skip", "repository",
                     "the Outcome was not enumerated, so its repository field "
                     "could not be read")]

    declared = (outcome.get("repository") or "").strip()
    if not declared:
        return [_row("fail", "repository",
                     "the published Outcome declares no repository")]

    # The drafts deliberately record the Zenodo **version DOI** rather than a
    # bare GitHub URL, because `github.com/ORG/REPO` names a moving target while
    # a version DOI pins the archived state the outcome was computed from. So a
    # DOI here is correct, and the check is that it resolves — not that it looks
    # like the git remote.
    if "doi.org/" in declared or declared.startswith("10."):
        try:
            result = resolve_doi(declared)
        except GroundingError as e:
            return [_row("fail", "repository",
                         f"the Outcome's repository DOI is malformed: {e}",
                         declared=declared)]
        if result["resolves"]:
            return [_row("pass", "repository",
                         f"the Outcome pins an archived version DOI that resolves "
                         f"— {result['doi']} ({result['title'][:50]})",
                         declared=declared, title=result["title"])]
        return [_row("fail", "repository",
                     f"the Outcome's repository DOI {result['doi']} does not "
                     f"resolve — the chain pins an archive that does not exist",
                     declared=declared)]

    if not repo_url:
        return [_row("info", "repository",
                     f"Outcome repository is {declared}; pass `repo_url` to check "
                     f"it against this repo")]

    want = repo_url.strip().rstrip("/").removesuffix(".git")
    if want.lower() in declared.lower() or declared.rstrip("/").lower() == want.lower():
        return [_row("pass", "repository",
                     f"the Outcome's repository matches: {declared}")]
    return [_row("fail", "repository",
                 f"the Outcome declares {declared!r}, which does not match "
                 f"{want!r} — the chain points at a different repository",
                 declared=declared, expected=want)]


def _local(term: str) -> str:
    """`http://purl.org/spar/cito/confirms` -> `confirms`."""
    return (term or "").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _norm_target(target: str) -> str:
    """Compare a DOI target regardless of its `https://doi.org/` prefix and case."""
    t = (target or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if t.lower().startswith(prefix):
            t = t[len(prefix):]
            break
    return t.lower()


def _assertion_triples(trig: str) -> list[tuple[str, str, str]]:
    """The (subject, predicate, object) triples of a nanopub's assertion graph."""
    import warnings

    from rdflib import Dataset, URIRef

    has_assertion = URIRef("http://www.nanopub.org/nschema#hasAssertion")
    with warnings.catch_warnings():
        # rdflib's own TriG parser uses its deprecated ConjunctiveGraph and
        # default_context; nothing here calls either.
        for message in ("Dataset.default_context", "ConjunctiveGraph is deprecated"):
            warnings.filterwarnings("ignore", message=message,
                                    category=DeprecationWarning)
        dataset = Dataset()
        dataset.parse(data=trig, format="trig")
        quads = list(dataset.quads((None, None, None, None)))
    graphs = {o for _s, p, o, _g in quads if p == has_assertion}
    return [(str(s), str(p), str(o)) for s, p, o, g in quads
            if getattr(g, "identifier", g) in graphs]


def read_cito(trig: str) -> dict:
    """The citing work(s) and the exact relation -> target pairs of a CiTO nanopub.

    Read from the nanopub itself rather than from the constellation: the walk
    reports a CiTO's `relations` and `citedTargets` as two unpaired lists, and
    places a CiTO at the apex or inside a chain depending on what else the walk
    reached. The TriG says exactly which relation points at which work.
    """
    citing: list[str] = []
    pairs: list[tuple[str, str]] = []
    for s, p, o in _assertion_triples(trig):
        if p.startswith(CITO):
            pairs.append((_local(p), o))
            if s not in citing:
                citing.append(s)
    return {"citing": citing, "pairs": pairs}


def read_quoted_paper(trig: str) -> str:
    """The DOI a Quote-with-comment quotes (`cito:quotes`), or "" for PICO/PCC."""
    for _s, p, o in _assertion_triples(trig):
        if p == CITO + "quotes":
            return o
    return ""


def _own_citation(published: dict, view: dict) -> dict:
    """The ledger's own CiTO: from its TriG when readable, else from the walk.

    The walk fallback is scoped to the ledger's chain — its own `citoRelations`,
    or the apex only when the apex IS step 06. Using the apex whenever the walk
    happened to contain a single chain is what let a neighbour's citation stand
    in for this one (forrt-research-mcp#11).
    """
    cito_uri = published.get("06", "")
    if not cito_uri:
        return {"source": "none", "citing": [], "pairs": []}
    try:
        cito = read_cito(fetch_trig(cito_uri))
    except Exception:  # noqa: BLE001 — unreadable TriG falls back to the walk
        cito = {"citing": [], "pairs": []}
    if cito["pairs"]:
        return {"source": "cito-nanopub", **cito}

    own = canonical_uri(published.get("05", ""))
    for chain in view["chains"]:
        if canonical_uri(chain.get("outcomeUri") or "") == own and chain.get("citoRelations"):
            return {"source": "walk-chain", "citing": [],
                    "pairs": [(_local(r), "") for r in chain["citoRelations"]]}
    apex = view.get("apexCito") or {}
    if apex and canonical_uri(apex.get("uri") or "") == canonical_uri(cito_uri):
        return {"source": "walk-apex", "citing": [],
                "pairs": [(_local(r), "") for r in apex.get("relations") or []]}
    return {"source": "none", "citing": [], "pairs": []}


def _quoted_paper(published: dict) -> str:
    quote = published.get("01", "")
    if not quote:
        return ""
    try:
        return read_quoted_paper(fetch_trig(quote))
    except Exception:  # noqa: BLE001 — PICO/PCC or unreadable: no quoted paper
        return ""


def _own_chain(published: dict, view: dict) -> dict | None:
    own = canonical_uri(published.get("05", ""))
    for chain in view["chains"]:
        if own and canonical_uri(chain.get("outcomeUri") or "") == own:
            return chain
    # A walk that found exactly one chain and does not enumerate its Outcome
    # under another URI is that chain.
    if len(view["chains"]) == 1 and not view["chains"][0].get("outcomeUri"):
        return view["chains"][0]
    return None


def _check_verdict_relation(published: dict, view: dict, own: dict,
                            quoted_paper: str) -> list[dict]:
    """The ledger's CiTO relation has to agree with the ledger's Outcome.

    `build_chain_draft.py` derives the relation from the verdict, so a mismatch
    means the two were published from different states — a chain claiming to
    confirm a paper it actually contradicted is the failure worth catching.

    Only the ledger's own Outcome and CiTO are judged. Published work is a
    network: other chains that cite the same paper turn up in the same walk,
    and their citations say nothing about this one.
    """
    chain = _own_chain(published, view)
    if chain is None:
        return [_row("warn", "verdict-relation",
                     "the ledger's Outcome (step 05) was not enumerated by the walk, "
                     "so its verdict could not be read — the CiTO relation was not "
                     "cross-checked")]
    verdict_label = chain.get("verdict") or ""
    verdict = verdict_label.replace(" ", "").lower()
    if not verdict:
        return [_row("warn", "verdict-relation",
                     "the ledger's Outcome carries no verdict — the CiTO relation "
                     "was not cross-checked")]
    if not own["pairs"]:
        return [_row("warn", "verdict-relation",
                     "the CiTO relations of step 06 could not be read — the "
                     "verdict was not cross-checked")]

    out: list[dict] = []
    outcome = canonical_uri(published.get("05", ""))
    if own["citing"] and outcome not in {canonical_uri(c) for c in own["citing"]}:
        out.append(_row("warn", "cito-citing-work",
                        f"step 06 cites from {', '.join(own['citing'])}, not from "
                        f"this ledger's Outcome", citing=own["citing"]))

    expected = RELATION_FROM_VERDICT.get(verdict)
    if expected is None:
        return out + [_row("info", "verdict-relation",
                           f"no expected CiTO relation is defined for verdict "
                           f"{verdict_label!r}", verdict=verdict_label)]

    pairs = own["pairs"]
    # Relations on a method paper, a dataset or a tutorial say nothing about the
    # claim. When the Quote names the paper, judge the relations on that paper.
    if quoted_paper and any(t for _r, t in pairs):
        on_paper = [(r, t) for r, t in pairs if _norm_target(t) == _norm_target(quoted_paper)]
        if not on_paper:
            return out + [_row("warn", "verdict-relation",
                               f"step 06 does not cite the quoted paper "
                               f"{quoted_paper} — the verdict was not cross-checked",
                               verdict=verdict_label, paper=quoted_paper,
                               actual=[f"{r} {t}" for r, t in pairs])]
        pairs = on_paper

    names = sorted({r for r, _t in pairs})
    verdict_vocabulary = {_local(v) for v in RELATION_FROM_VERDICT.values()}
    if not verdict_vocabulary.intersection(names):
        # The chain does not cite the target with a verdict-bearing relation
        # at all — e.g. extends, citesAsAuthority, citesAsDataSource. That is a
        # different kind of citation, not a disagreement with the Outcome.
        return out + [_row("info", "verdict-relation",
                           f"cited with {', '.join(names)}, which asserts no "
                           f"verdict on the cited work — no cross-check applies "
                           f"(verdict is {verdict_label})",
                           verdict=verdict_label, actual=names)]
    if _local(expected) in names:
        return out + [_row("pass", "verdict-relation",
                           f"verdict {verdict_label} matches CiTO {_local(expected)}")]
    return out + [_row("fail", "verdict-relation",
                       f"verdict {verdict_label} implies CiTO {_local(expected)}, "
                       f"but the chain cites with {', '.join(names)} — the Outcome "
                       f"and the Citation disagree about what this replication found",
                       verdict=verdict_label, expected=expected, actual=names)]


def _neighbour_context(published: dict, view: dict) -> list[dict]:
    """Other chains the walk reached: context, never a pass or a fail."""
    own = canonical_uri(published.get("05", ""))
    others = [c for c in view["chains"]
              if c.get("outcomeUri") and canonical_uri(c["outcomeUri"]) != own]
    if not others:
        return []
    described = [f"{c['outcomeUri']} ({c.get('verdict') or 'no verdict'}"
                 f"{'; ' + ', '.join(_local(r) for r in c['citoRelations']) if c.get('citoRelations') else ''})"
                 for c in others]
    return [_row("info", "neighbours",
                 f"{len(others)} other chain(s) in this constellation are not in this "
                 f"ledger and were not judged: {'; '.join(described)}",
                 outcomes=[c["outcomeUri"] for c in others])]


def _own_cited_paper(view: dict, own: dict, quoted_paper: str) -> dict:
    """The work this chain is of, from its own CiTO (forrt-research-mcp#9).

    Preference: the verdict relation's target that is the quoted paper, any
    verdict relation's target, the quoted paper if cited at all, the first DOI.
    Falls back to the walk's answer when the CiTO's targets are unknown.
    """
    reported = view["citedPaper"]
    targets = [t for _r, t in own["pairs"] if t]
    if not targets:
        return reported
    unique = list(dict.fromkeys(targets))
    verdict_vocabulary = {_local(v) for v in RELATION_FROM_VERDICT.values()}
    verdict_targets = [t for r, t in own["pairs"] if t and r in verdict_vocabulary]
    quoted = [t for t in unique if quoted_paper and _norm_target(t) == _norm_target(quoted_paper)]
    if quoted and quoted[0] in verdict_targets:
        doi, selected_by = quoted[0], "verdict-relation-on-quoted-paper"
    elif verdict_targets:
        doi, selected_by = verdict_targets[0], "verdict-relation"
    elif quoted:
        doi, selected_by = quoted[0], "quoted-paper"
    else:
        dois = [t for t in unique if _looks_like_doi(t)]
        doi, selected_by = (dois or unique)[0], "first-doi" if dois else "first-target"
    reported_doi = reported.get("reportedPaperDoi") or ""
    return {
        "doi": doi,
        "allCitedTargets": unique,
        "source": own["source"],
        "selectedBy": selected_by,
        "reportedPaperDoi": reported_doi,
        "disagreesWithReported": bool(reported_doi)
        and _norm_target(reported_doi) != _norm_target(doi),
    }


def _check_cited_targets(paper: dict, mode: str) -> list[dict]:
    """Everything the chain cites must exist.

    A CiTO target is not always a DOI. A chain may credit a software repository
    by URL — both of this project's question-rooted chains do, one citing
    `github.com/GRID4EARTH/healpix-analyse` — so treating every target as a DOI
    reports a correctly published chain as broken.
    """
    targets = paper.get("allCitedTargets") or ([paper["doi"]] if paper["doi"] else [])
    if not targets:
        if mode == "new_research":
            return [_row("info", "cited-target",
                         "the chain cites nothing, which is expected for research "
                         "that does not start from an existing work")]
        return [_row("fail", "cited-target",
                     f"the chain cites nothing — nothing identifies the work this "
                     f"{mode} is of. If this study started from scratch, pass "
                     f"mode='new_research'")]

    out: list[dict] = []
    for target in targets:
        if _looks_like_doi(target):
            out.append(_check_cited_doi(target))
        else:
            out.append(_check_cited_url(target))
    return out


def _looks_like_doi(target: str) -> bool:
    t = (target or "").strip()
    return t.startswith("10.") or "doi.org/" in t


def _check_cited_doi(target: str) -> dict:
    try:
        result = resolve_doi(target)
    except GroundingError as e:
        return _row("fail", "cited-target", f"{target} is a malformed DOI: {e}",
                    target=target)
    if result["resolves"]:
        return _row("pass", "cited-target",
                    f"{result['doi']} resolves — {result['title'][:60]}",
                    target=result["doi"], title=result["title"])
    if result["status"] == 404:
        return _row("fail", "cited-target",
                    f"{result['doi']} IS NOT REGISTERED — the published chain "
                    f"cites a DOI that does not exist", target=result["doi"])
    return _row("warn", "cited-target",
                f"{result['doi']} could not be confirmed (HTTP {result['status']}); "
                f"may be transient", target=result["doi"])


def _check_cited_url(target: str) -> dict:
    """A non-DOI citation target — typically a software repository."""
    if not target.startswith(("http://", "https://")):
        return _row("fail", "cited-target",
                    f"{target!r} is neither a DOI nor a URL — nothing resolves it",
                    target=target)
    try:
        fetch_url_ok = _url_reachable(target)
    except ApiError as e:
        return _row("warn", "cited-target",
                    f"{target} could not be reached ({e}); may be transient",
                    target=target)
    if fetch_url_ok:
        return _row("pass", "cited-target",
                    f"{target} resolves (a non-DOI target, typically a software "
                    f"repository)", target=target)
    return _row("fail", "cited-target",
                f"{target} does not resolve — the published chain cites a URL "
                f"that is not there", target=target)


def _url_reachable(url: str, *, timeout: int = 30) -> bool:
    import urllib.error
    import urllib.request

    from .api import USER_AGENT

    request = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as e:
        if e.code == 405:  # HEAD not allowed; the resource still exists
            return True
        if e.code == 404:
            return False
        raise ApiError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ApiError(str(e)) from e


def _resolve_mode(mode: str, published: dict) -> str:
    """Which of the three shapes this chain is.

    `auto` infers it: a chain with no CiTO step published did not start from an
    existing work. Reproduction and replication are not distinguishable from the
    ledger alone (the difference lives in the Study's `type` field, which the
    constellation does not expose), and they verify identically — so `auto`
    reports the neutral `replication` for both. Pass the mode explicitly when
    you want the message wording to match your study.
    """
    if mode not in MODES:
        raise ApiError(f"unknown mode {mode!r}; expected one of {', '.join(MODES)}")
    if mode != "auto":
        return mode
    return "replication" if "06" in published else "new_research"


def verify_chain(published_path: str, repo_url: str = "", mode: str = "auto") -> dict:
    """Verify the chain listed in a `nanopubs/PUBLISHED.md` ledger.

    `mode` is one of `auto` (default), `replication`, `reproduction` or
    `new_research`. It changes only what is *required*: research that starts
    from scratch has no existing work to cite, so no CiTO step and no cited DOI
    are expected. Everything else is checked identically in all three.
    """
    path = Path(published_path).expanduser()
    if path.is_dir():
        path = path / "PUBLISHED.md"
    if not path.is_file():
        raise ApiError(f"no ledger at {path}")

    published = parse_published(path.read_text())
    if not published:
        raise ApiError(
            f"no published URIs found in {path} — expected table rows like "
            f"`| 05 | … | https://w3id.org/np/RA… |`")

    resolved_mode = _resolve_mode(mode, published)
    required = (REQUIRED_STEPS_NEW_RESEARCH if resolved_mode == "new_research"
                else REQUIRED_STEPS)

    rows: list[dict] = []

    # Ledger layouts vary. If the file holds URIs the step table did not
    # account for, the steps are not missing — this parser cannot read them,
    # and saying "incomplete" would be a false verdict about a real chain.
    stray = unparsed_uris(path.read_text(), published)
    missing = [s for s in required if s not in published]
    if stray and missing:
        rows.append(_row(
            "fail", "ledger",
            f"{len(stray)} nanopub URI(s) in this ledger are outside the "
            f"`| NN | … |` step table, so {len(missing)} step(s) look missing "
            f"that may well be published. This ledger uses a layout this tool "
            f"cannot map to step numbers — check it by hand",
            unparsedUris=stray, apparentlyMissing=missing))
    else:
        for step in missing:
            rows.append(_row("fail", "ledger",
                             f"step {step} ({STEP_NAMES[step]}) has no URI — the "
                             f"chain is incomplete", step=step))
    if resolved_mode == "new_research" and "06" not in published:
        rows.append(_row("info", "ledger",
                         "no CiTO step, which is expected for research that does "
                         "not start from an existing work"))

    view = _summary(_entry_uri(published))

    in_constellation = {canonical_uri(u) for u in (
        [c["outcomeUri"] for c in view["chains"]]
        + [s["uri"] for c in view["chains"] for s in c["steps"]]
        + [u["uri"] for u in view["upstream"]]
        + ([view["apexCito"]["uri"]] if view["apexCito"] else [])
        + ([view["researchSynthesis"]["uri"]] if view["researchSynthesis"] else [])
    ) if u}

    for step, uri in sorted(published.items()):
        rows.append(_check_reachable(step, uri, in_constellation))

    rows += _check_repository(view, published, repo_url)
    own = _own_citation(published, view)
    quoted_paper = _quoted_paper(published) if own["pairs"] else ""
    if "06" in published:
        rows += _check_verdict_relation(published, view, own, quoted_paper)
    rows += _neighbour_context(published, view)
    cited_paper = _own_cited_paper(view, own, quoted_paper)
    rows += _check_cited_targets(cited_paper, resolved_mode)

    counts = {s: sum(1 for r in rows if r["status"] == s)
              for s in ("pass", "fail", "warn", "skip", "info")}
    return {
        "ledger": str(path),
        "mode": resolved_mode,
        "stepsPublished": sorted(published),
        "citedPaper": cited_paper,
        "green": counts["fail"] == 0,
        "counts": counts,
        "rows": sorted(rows, key=lambda r: ("fail", "warn", "skip", "info", "pass")
                       .index(r["status"])),
    }
