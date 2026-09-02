"""MCP server for producing verifiable FORRT nanopublication chains.

Serves replications and reproductions today. New research from a research
question is served for the Question -> AIDA -> Claim half of the chain; the
Study/Outcome half is replication-shaped in the current Science Live templates
(`scope` = "what part of the claim is reproduced", `validationStatus` =
Validated/Contradicted *of an original*) and needs new templates upstream. See
the README's Scope section — do not bend those fields to fit primary research.

Run:  forrt-research-mcp          (stdio transport)
Add user-scoped:
    pipx install forrt-research-mcp
    claude mcp add forrt-research -s user -- forrt-research-mcp

Companion to `replication-radar` (which DISCOVERS what to replicate) and the
OpenAIRE MCP (which searches the literature). This server does neither — it
helps a researcher *produce* a correct chain, and verify one that exists.

Environment:
    SCIENCELIVE_API_BASE   default https://api-dev.sciencelive4all.org
    SCIENCELIVE_API_KEY    optional; /np/constellation is a public read
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .api import ApiError
from .constellation import fetch as _fetch
from .constellation import prior_work as _prior_work
from .constellation import summary as _summary
from .quotes import QuoteError
from .quotes import verify_quote as _verify_quote

mcp = FastMCP("forrt-research")


def _fail(error: Exception) -> dict:
    """Surface a failure as data, verbatim, rather than raising into the client.

    The agent must relay what actually broke — a paraphrased error costs a
    researcher the one clue they need (a 500 from the API is not the same
    problem as an unresolvable URI).
    """
    return {"ok": False, "error": str(error), "errorType": type(error).__name__}


@mcp.tool()
def constellation(uri: str, depth: int = 5, max_nodes: int = 80) -> dict:
    """The FORRT chain(s) reachable from a published nanopub URI, as a compact
    projection you can actually read.

    Call this to see an existing chain — before starting a replication (what has
    already been done?), when extending someone else's chain, or to inspect your
    own after publishing.

    The raw Science Live constellation is ~330 KB for one chain, of which ~95 %
    is a depth-5 neighbourhood of UNRELATED chains reachable through shared
    links (on the marine-heatwave chain, 64 of 98 nodes are other studies' AIDA
    statements). This returns the chains, the apex CiTO, any Research Synthesis,
    and the Quote/Question anchors attributable to this paper — and drops the
    rest, reporting how much it dropped under `neighbourhood`.

    Read `replicatedPaper` rather than assuming: the API's own top-level
    `paperDoi` is a frequency vote across the whole walk and can name a
    neighbour's paper, so this derives the paper from the chain's CiTO citation
    and sets `disagreesWithReported` when the two differ.

    `stepsPresent` legitimately omits steps: a CiTO at the apex of the
    constellation is hoisted out of its chain, and Quote/AIDA anchors are often
    not enumerated. Missing does not mean unpublished.
    """
    try:
        return {"ok": True, **_summary(uri, depth=depth, max_nodes=max_nodes)}
    except ApiError as e:
        return _fail(e)


@mcp.tool()
def prior_work(uri: str) -> dict:
    """What has already been claimed about a paper — the starting point for new
    work, whether that work is a replication or a fresh study.

    Given any published nanopub URI in a constellation, returns one entry per
    completed chain: the claim type, what was tested (`scope`), how (`method`),
    what was done differently (`deviations`), the verdict and confidence, and —
    the field to read most carefully — `limitations`, where the previous authors
    stated in their own signed words what their study did NOT cover.

    Use it to avoid duplicating an existing replication, to choose a CiTO
    relation relative to prior work (`extends` / `qualifies` / `disputes`), and
    to find the part of a claim still open. Cite what you find; do not re-derive
    it — a published chain step is a record with a URI, not a result to recompute.
    """
    try:
        return {"ok": True, **_prior_work(uri)}
    except ApiError as e:
        return _fail(e)


@mcp.tool()
def verify_quote(pdf_path: str, quotation: str) -> dict:
    """Prove that a candidate quotation is really in a source PDF, before it is
    published as verbatim.

    Call this on EVERY quotation destined for a Quote-with-comment nanopub. You
    choose which sentence carries the paper's claim — that is judgement. This
    decides whether the sentence is admissible, and that is not: it is a string
    search, and anyone can re-run it and get the same answer.

    Returns a graded verdict with the page, character offsets and the file's
    SHA-256 as evidence:

      exact                byte-identical to the extracted page text
      normalized           matched after whitespace / ligature / typographic
                           punctuation / line-break-hyphen repair
      extraction_tolerant  additionally ignored hyphens and punctuation spacing
      not_found            NOT in this PDF — do not publish it as a quotation

    A tier below `exact` is normal and not a warning about your quotation: PDF
    extraction inserts line breaks and drops hyphens (a real published FORRT
    quotation matches only at `extraction_tolerant`, because pypdf reads
    "35-year" as "35year"). Every tier canonicalises formatting only — never
    words, digits or order — so an altered number still fails at every tier.
    Read `matched_text` before publishing.

    On `not_found`, `closest.text_in_pdf` shows what the paper says where it
    nearly matched. A one-digit change scores ~0.91 similarity and is still
    `not_found`: high similarity is not a pass.
    """
    try:
        return {"ok": True, **_verify_quote(pdf_path, quotation)}
    except QuoteError as e:
        return _fail(e)


@mcp.tool()
def constellation_raw(uri: str, depth: int = 5, max_nodes: int = 80) -> dict:
    """The unprojected `/np/constellation` response, including every node and
    edge.

    For debugging the graph itself or investigating an upstream data problem.
    Prefer `constellation` for normal use: this is very large (~330 KB for a
    single chain) and most of it belongs to other studies. Lower `depth` to
    shrink the walk.
    """
    try:
        return {"ok": True, **_fetch(uri, depth=depth, max_nodes=max_nodes)}
    except ApiError as e:
        return _fail(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
