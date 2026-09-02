"""Compact projections of a Science Live constellation.

`/np/constellation` walks the FORRT citation graph bidirectionally and returns
everything it reaches: `chains[]` (the useful part) plus flat `nodes[]`/`edges[]`
for debugging. On a real chain that is ~330 KB, of which the chains are ~4 %.
The rest is a depth-5 neighbourhood of *unrelated* chains that happen to be
reachable — on the marine-heatwave chain, 64 of 98 nodes are AIDA statements
from other studies entirely.

Handing that to an agent wastes its context and invites it to reason over
another paper's claims. Every function here returns a projection instead.

Two upstream quirks are handled rather than worked around by the caller:

1. **The CiTO is hoisted.** When a chain's CiTO sits at the apex of the whole
   constellation it is moved to top-level `apexCito` and removed from
   `chains[].steps[]`, so a chain legitimately shows no CiTO step.
2. **`quote.quotedText` is empty** on nanopubs that use the current quote
   template (verified 2026-09-02). The text is present in `label` and in
   `plainTextExcerpts`, so we recover it from there and say where it came from.
3. **Top-level `paperDoi` can name the wrong paper.** It is chosen by frequency
   across the whole walk, so a neighbouring study's Quote nanopubs can outvote
   the chain's own citation. On the marine-heatwave chain it reports the
   LifeWatch ERIC paper (4 unrelated quotes) rather than Oliver et al. 2018,
   which the chain's CiTO actually cites. We therefore derive the replicated
   paper from the CiTO's `citedTargets` and only fall back to `paperDoi`,
   reporting which source was used and flagging any disagreement.
"""
from __future__ import annotations

from typing import Any

from .api import get_json

# Steps in FORRT chain order. Publishing and verification both depend on it.
CHAIN_ORDER = ["Quote", "Question", "AIDA", "Claim", "Study", "Outcome", "CiTO"]

_UPSTREAM_KINDS = ("quote", "question")


def fetch(uri: str, *, depth: int = 5, max_nodes: int = 80,
          base: str | None = None, key: str | None = None) -> dict:
    """Fetch the raw constellation for `uri`. Prefer `summary()` for agent use."""
    return get_json(
        "/np/constellation",
        {"uri": uri, "depth": str(depth), "maxNodes": str(max_nodes)},
        base=base, key=key,
    )


_LABEL_PREFIXES = ("Paper annotation:", "AIDA sentence:", "Quote:")


def _label_stem(label: str) -> str:
    """The distinctive head of a node label, with its template prefix and the
    trailing ellipsis removed. Used to tell a quote's own text apart from the
    annotator's comment when both arrive as untagged excerpts."""
    stem = (label or "").strip()
    for prefix in _LABEL_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix):].strip()
            break
    return stem.rstrip(".").strip()


def _quote_text_and_comment(node: dict) -> tuple[str, str, str]:
    """Return (quoted_text, comment, provenance) for a quote node.

    The structured extractor is preferred. When it is empty (quirk 2) the node
    carries two untagged long excerpts — the quotation and the annotator's
    comment — in no guaranteed order. The label echoes the *quotation*, so we
    match excerpts against the label stem to decide which is which instead of
    assuming a position.
    """
    q = node.get("quote") or {}
    text, comment = (q.get("quotedText") or "").strip(), (q.get("comment") or "").strip()
    if text:
        return text, comment, "structured"

    excerpts = [e.strip() for e in (node.get("plainTextExcerpts") or []) if e and e.strip()]
    long_ones = [e for e in excerpts if len(e) > 40]
    if not long_ones:
        label = (node.get("label") or "").strip()
        return (label, "", "label-fallback") if label else ("", "", "missing")

    stem = _label_stem(node.get("label", ""))
    probe = stem[:40].lower()
    quoted = next((e for e in long_ones if probe and e.lower().startswith(probe)), "")
    if not quoted:
        # No label match: report the label stem itself rather than guess an excerpt.
        return stem, "", "label-fallback"

    others = [e for e in long_ones if e is not quoted]
    return quoted, (others[0] if others else ""), "excerpt-matched-to-label"


def replicated_paper(raw: dict) -> dict:
    """Which paper this constellation's chains actually cite.

    Authoritative source is the CiTO's `citedTargets` (that IS the citation).
    Top-level `paperDoi` is a frequency vote across the whole walk and can name
    a neighbour's paper — see quirk 3 — so it is only a fallback, and a
    disagreement is reported rather than resolved silently.
    """
    reported = (raw.get("paperDoi") or "").strip()

    cited: list[str] = []
    apex = raw.get("apexCito") or {}
    cited.extend(apex.get("citedTargets") or [])
    for node in raw.get("nodes") or []:
        if node.get("stepKind") == "cito":
            cited.extend((node.get("cito") or {}).get("citedTargets") or [])
    for chain in raw.get("chains") or []:
        for step in chain.get("steps") or []:
            if step.get("step") == "CiTO":
                cited.extend(step.get("targets") or [])

    unique = sorted({c.strip() for c in cited if c and c.strip()})
    if unique:
        return {
            "doi": unique[0],
            "allCitedTargets": unique,
            "source": "cito-citedTargets",
            "reportedPaperDoi": reported,
            "disagreesWithReported": bool(reported) and reported not in unique,
        }
    return {
        "doi": reported,
        "allCitedTargets": [],
        "source": "paperDoi-fallback" if reported else "unknown",
        "reportedPaperDoi": reported,
        "disagreesWithReported": False,
    }


def _upstream_nodes(raw: dict, paper_doi: str) -> list[dict]:
    """Quote / Question nodes attributable to `paper_doi`.

    Upstream anchors sit above the AIDA and are not enumerated in `chains[]`.
    Filtering by the *cited* paper is what keeps a neighbouring study's quotes
    out — the depth-5 walk reaches plenty of them.
    """
    target = (paper_doi or "").strip().lower()
    out: list[dict] = []

    for node in raw.get("nodes") or []:
        if node.get("stepKind") not in _UPSTREAM_KINDS:
            continue

        cited = ((node.get("quote") or {}).get("citedDoi") or "").strip().lower()
        if target and cited != target:
            continue  # a different paper's anchor, or unattributable

        text, comment, provenance = _quote_text_and_comment(node)
        out.append({
            "step": "Quote" if node["stepKind"] == "quote" else "Question",
            "uri": node["uri"],
            "text": text,
            "text_source": provenance,
            "comment": comment,
            "citedDoi": (node.get("quote") or {}).get("citedDoi") or "",
            "date": node.get("date") or "",
            "creators": node.get("creatorNames") or [],
        })

    out.sort(key=lambda n: n["date"])
    return out


def summary(uri: str, *, depth: int = 5, max_nodes: int = 80,
            base: str | None = None, key: str | None = None,
            raw: dict | None = None) -> dict:
    """A compact, agent-sized view of the constellation reachable from `uri`.

    Drops `nodes[]`/`edges[]` (the bulk, and mostly unrelated chains) and keeps
    the chains, the apex CiTO, any Research Synthesis, and the upstream Quote /
    Question anchors attributable to this paper.
    """
    data = raw if raw is not None else fetch(
        uri, depth=depth, max_nodes=max_nodes, base=base, key=key
    )

    chains = []
    for chain in data.get("chains") or []:
        steps = list(chain.get("steps") or [])
        steps.sort(key=lambda s: CHAIN_ORDER.index(s["step"])
                   if s.get("step") in CHAIN_ORDER else len(CHAIN_ORDER))
        chains.append({
            "id": chain.get("id") or "",
            "outcomeUri": chain.get("outcomeUri") or "",
            "verdict": chain.get("outcomeVerdict") or "",
            "confidence": chain.get("outcomeConfidence") or "",
            "citoRelations": chain.get("citoRelations") or [],
            "steps": steps,
            "stepsPresent": [s.get("step") for s in steps],
        })

    apex = data.get("apexCito") or None
    synthesis = data.get("researchSynthesis") or None
    paper = replicated_paper(data)

    return {
        "entry": data.get("entry") or uri,
        "replicatedPaper": paper,
        "chains": chains,
        "upstream": _upstream_nodes(data, paper["doi"]),
        "apexCito": {
            "uri": apex.get("uri", ""),
            "relations": apex.get("relations", []),
            "citedTargets": apex.get("citedTargets", []),
        } if apex else None,
        "researchSynthesis": {
            "uri": synthesis.get("uri", ""),
            "label": synthesis.get("label", ""),
        } if synthesis else None,
        "externalCitations": data.get("externalCitations") or [],
        # Kept so a caller can see how much was dropped, without carrying it.
        "neighbourhood": {
            "nodeCount": data.get("nodeCount", 0),
            "edgeCount": data.get("edgeCount", 0),
            "note": ("nodes/edges omitted: a depth-%d walk reaches unrelated chains. "
                     "Re-fetch with view='raw' if you need the full graph." % depth),
        },
    }


def prior_work(uri: str, *, base: str | None = None, key: str | None = None,
               raw: dict | None = None) -> dict:
    """What has already been claimed about this paper — the starting point for
    new work.

    Returns one entry per published chain: what was tested, how, what the
    verdict was, and what the authors themselves said was NOT tested (the
    Outcome's `limitations`). That last field is where a new study finds the
    part of the claim still open.
    """
    view = summary(uri, base=base, key=key, raw=raw)

    entries = []
    for chain in view["chains"]:
        by_step = {s["step"]: s for s in chain["steps"]}
        study = by_step.get("Study", {})
        outcome = by_step.get("Outcome", {})
        entries.append({
            "outcomeUri": chain["outcomeUri"],
            "verdict": chain["verdict"],
            "confidence": chain["confidence"],
            "claimType": by_step.get("Claim", {}).get("type", ""),
            "scope": study.get("scope", ""),
            "method": study.get("method", ""),
            "deviations": study.get("deviations", ""),
            "conclusion": outcome.get("conclusion", ""),
            "limitations": outcome.get("limitations", ""),
            "repository": outcome.get("repository", ""),
            "citoRelations": chain["citoRelations"],
        })

    return {
        "replicatedPaper": view["replicatedPaper"],
        "replicationCount": len(entries),
        "verdicts": sorted({e["verdict"] for e in entries if e["verdict"]}),
        "priorWork": entries,
        "upstreamAnchors": view["upstream"],
        "researchSynthesis": view["researchSynthesis"],
    }
