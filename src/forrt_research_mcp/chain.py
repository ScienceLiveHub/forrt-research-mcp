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
_ROW_RE = re.compile(
    r"^\|\s*(\d{2})\s*\|([^|]*)\|\s*(\S+)\s*\|", re.M)
_URI_RE = re.compile(r"^https://w3id\.org/(?:sciencelive/)?np/RA[A-Za-z0-9_-]{20,}$")

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
    """Step number -> published URI, skipping rows with no URI yet."""
    found: dict[str, str] = {}
    for step, _template, uri in _ROW_RE.findall(text):
        uri = uri.strip().strip("`")
        if _URI_RE.match(uri):
            found[step] = uri
    return found


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


def _check_verdict_relation(view: dict, apex_relations: list[str]) -> list[dict]:
    """The CiTO relation has to agree with the Outcome it cites from.

    `build_chain_draft.py` derives the relation from the verdict, so a mismatch
    means the two were published from different states — a chain claiming to
    confirm a paper it actually contradicted is the failure worth catching.
    """
    out: list[dict] = []
    single_chain = len(view["chains"]) == 1
    for chain in view["chains"]:
        verdict = (chain.get("verdict") or "").replace(" ", "").lower()
        relations = chain.get("citoRelations") or []
        # When this chain's CiTO sits at the apex of the constellation it is
        # hoisted out of chains[].steps[] and its relations go with it, so an
        # empty list here does not mean the chain has no citation.
        if not relations and single_chain:
            relations = apex_relations
        if not verdict or not relations:
            continue
        expected = RELATION_FROM_VERDICT.get(verdict)
        if expected is None:
            out.append(_row("info", "verdict-relation",
                            f"no expected CiTO relation is defined for verdict "
                            f"{chain['verdict']!r}", verdict=chain["verdict"]))
            continue
        names = [r.rsplit("/", 1)[-1] for r in relations]
        verdict_vocabulary = {v.rsplit("/", 1)[-1] for v in RELATION_FROM_VERDICT.values()}
        if not verdict_vocabulary.intersection(names):
            # The chain does not cite the target with a verdict-bearing relation
            # at all — e.g. citesAsAuthority for a method paper, or
            # citesAsDataSource. That is a different kind of citation, not a
            # disagreement with the Outcome, so there is nothing to cross-check.
            out.append(_row("info", "verdict-relation",
                            f"cited with {', '.join(names)}, which asserts no "
                            f"verdict on the cited work — no cross-check applies "
                            f"(verdict is {chain['verdict']})",
                            verdict=chain["verdict"], actual=relations))
            continue
        if expected.rsplit("/", 1)[-1] in names:
            out.append(_row("pass", "verdict-relation",
                            f"verdict {chain['verdict']} matches CiTO "
                            f"{expected.rsplit('/', 1)[-1]}"))
        else:
            out.append(_row("fail", "verdict-relation",
                            f"verdict {chain['verdict']} implies CiTO "
                            f"{expected.rsplit('/', 1)[-1]}, but the chain cites "
                            f"with {', '.join(names)} — the Outcome and the "
                            f"Citation disagree about what this replication found",
                            verdict=chain["verdict"], expected=expected,
                            actual=relations))
    return out


def _check_cited_targets(view: dict, mode: str) -> list[dict]:
    """Everything the chain cites must exist.

    A CiTO target is not always a DOI. A chain may credit a software repository
    by URL — both of this project's question-rooted chains do, one citing
    `github.com/GRID4EARTH/healpix-analyse` — so treating every target as a DOI
    reports a correctly published chain as broken.
    """
    paper = view["citedPaper"]
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
    for step in [s for s in required if s not in published]:
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
    apex_relations = (view["apexCito"] or {}).get("relations", [])
    rows += _check_verdict_relation(view, apex_relations)
    rows += _check_cited_targets(view, resolved_mode)

    counts = {s: sum(1 for r in rows if r["status"] == s)
              for s in ("pass", "fail", "warn", "skip", "info")}
    return {
        "ledger": str(path),
        "mode": resolved_mode,
        "stepsPublished": sorted(published),
        "citedPaper": view["citedPaper"],
        "green": counts["fail"] == 0,
        "counts": counts,
        "rows": sorted(rows, key=lambda r: ("fail", "warn", "skip", "info", "pass")
                       .index(r["status"])),
    }
