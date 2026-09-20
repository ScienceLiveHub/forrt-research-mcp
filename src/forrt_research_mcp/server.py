"""MCP server for producing verifiable FORRT nanopublication chains.

Serves all three shapes of study: reproduction (same data, same methods),
replication (different data and/or methods), and research that starts from
scratch. The chain is the same either way — anchor (Quote or PICO/PCC question)
-> AIDA -> Claim -> Study -> Outcome -> CiTO — and for a from-scratch study the
Claim is your own hypothesis, which makes the Claim-before-Study order a
pre-registration rather than a mismatch. See the README's Scope section for the
one place the templates still assume an original.

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
from .chain import verify_chain as _verify_chain
from .drafts import validate_draft as _validate_draft
from .drafts import validate_drafts as _validate_drafts
from .grounding import GroundingError
from .grounding import resolve_doi as _resolve_doi
from .grounding import wikidata_lookup as _wikidata_lookup
from .quotes import QuoteError
from .quotes import verify_quote as _verify_quote
from .templates import VOCABULARIES
from .templates import steps as _steps
from .templates import template_fields as _template_fields
from .templates import vocabulary as _vocabulary

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

    Read `citedPaper` rather than assuming: the API's own top-level
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


@mcp.tool()
def template_fields(step: str, live: bool = True) -> dict:
    """The exact form fields of one FORRT chain step, from the live template.

    Call this BEFORE drafting any nanopub field. A nanopub template *is* the
    schema for its step, so this is what makes "never invent a field name" a
    lookup rather than a rule you have to remember: it returns the real field
    ids, prompts, whether each is required or repeatable, the length/format
    constraints (`regex`, `prefix`, `datatype` — where the Quote template's
    character cap actually lives), and, for choice fields, the allowed values.

    `step` accepts `05_outcome`, `05`, or `outcome`. Known steps: 01_quote,
    01_pico, 01_pcc, 02_aida, 03_claim, 04_study, 05_outcome, 06_citation,
    07_research_software, 08_synthesis.

    Check `source`: `live` means fetched from the nanopub network just now;
    `bundled-snapshot` means the network was unavailable and these are vendored
    values that may be stale. `driftedFromSnapshot: true` means the template was
    superseded upstream — the live values win, and this package needs re-vendoring.
    """
    try:
        return {"ok": True, **_template_fields(step, live=live)}
    except ApiError as e:
        return _fail(e)


@mcp.tool()
def vocabulary(name: str, live: bool = True) -> dict:
    """The allowed values of a FORRT controlled vocabulary, from its template.

    Use it whenever a draft needs a claim type, a study type, a validation
    status, a confidence level, or a CiTO relation. Every term comes from the
    real restricted-choice field on the real template (or the value-list nanopub
    it points at), so a value returned here is one the form will actually accept
    — and nothing else is.

    Names: claim_type, study_type, validation_status, confidence_level,
    cito_relation, pico_question_type.

    Three worth reading before you draft:
      - `study_type` carries the Reproduction vs Replication distinction
        (same data + same methods, vs different data and/or methods, or both).
      - `validation_status` is the Outcome verdict. Pick it from the evidence,
        not from what would be a nicer result; a contradicted replication is
        publishable and an overclaimed one is not.
      - `pico_question_type` is PICO-only, deliberately. Step 01 has three
        alternative anchors and they are not variants of one form: a PCC
        question has NO type field, and a Quote-with-comment has neither a type
        nor a label. Call `template_fields` on the anchor you are actually using
        (01_quote, 01_pico or 01_pcc) rather than assuming they match.
    """
    try:
        return {"ok": True, **_vocabulary(name, live=live)}
    except ApiError as e:
        return _fail(e)


@mcp.tool()
def list_schemas() -> dict:
    """Which chain steps and vocabularies this server can look up.

    Cheap, offline, and no network. Call it first if you are unsure what to pass
    to `template_fields` or `vocabulary`.
    """
    return {
        "ok": True,
        "steps": _steps(),
        "vocabularies": {
            name: {"step": step, "field": field}
            for name, (step, field) in sorted(VOCABULARIES.items())
        },
    }


@mcp.tool()
def resolve_doi(doi: str) -> dict:
    """Does this DOI resolve, and to what?

    Call it on every DOI destined for a nanopub field, `CITATION.cff`, or a CiTO
    citation. `resolves: false` means the DOI is not registered — do not publish
    it, however well-formed it looks. A well-formed DOI is not a real one, and a
    fabricated one is indistinguishable from a genuine one until something asks
    the registry.

    On success it returns the registered title, authors, year, container and
    type, so you can confirm it is the paper you mean rather than merely a paper
    that exists. Accepts bare (`10.…`), URL, or `doi:`-prefixed forms.
    """
    try:
        return {"ok": True, **_resolve_doi(doi)}
    except GroundingError as e:
        return _fail(e)


@mcp.tool()
def wikidata_lookup(query: str, expected_type: str = "", limit: int = 5) -> dict:
    """Find real Wikidata items for a term, and type-check them.

    Call it for every Wikidata topic or keyword destined for a nanopub field.
    It returns candidates with their descriptions and real P31/P279 types; it
    deliberately does NOT choose one, because picking the right sense of an
    ambiguous label is a judgement. What it guarantees is that the QID you
    publish exists and is what you say it is.

    Pass `expected_type` as a QID (e.g. `Q16521` taxon, `Q11862829` academic
    discipline) and each candidate is marked `typeMatches` from its actual
    statements. Candidates are annotated, never filtered — a near miss is often
    the informative result. Searching "Bombus" with `Q16521`, for instance,
    returns the insect genus as a match and the album of the same name as not.

    A zero-candidate result means leave the field empty or try another label.
    Never fall back to a QID from memory.
    """
    try:
        return {"ok": True, **_wikidata_lookup(query, expected_type, limit)}
    except GroundingError as e:
        return _fail(e)


@mcp.tool()
def validate_draft(path: str, step: str = "", live: bool = True) -> dict:
    """Check one drafted nanopub against its template and the real world.

    Run this on every draft before publishing. It is the pre-flight checklist in
    `docs/forrt-form-fields.md`, actually executed: field ids checked against the
    live template, choice values against the template's own enumeration, length
    caps against its regex, DOIs against the registry, Wikidata QIDs against
    Wikidata.

    `publishable` is true only when there are no errors. Severities:

      error    would publish something false, or be rejected by the form
      warning  a human should look, but it may be intentional
      info     checked and fine, or deliberately not checked

    Three placeholder conventions are distinguished, because only one is a
    problem: `«URI of step 05 …»` is a back-reference the chain wizard fills
    (info); `{{ZENODO_VERSION_DOI}}` is a release-time token the release
    workflow substitutes (warning — confirm the release ran); anything else
    still standing in for a value is an error.

    `step` is inferred from the filename (`05_outcome.md`); pass it explicitly
    for a file named otherwise. A draft whose required fields are nearly all
    empty is reported once as an unfilled skeleton rather than field by field.
    """
    try:
        return {"ok": True, **_validate_draft(path, step, live=live)}
    except ApiError as e:
        return _fail(e)


@mcp.tool()
def validate_drafts(directory: str, live: bool = True) -> dict:
    """Check every draft in a `nanopubs/drafts/` directory at once.

    The whole-chain pre-flight: run it before starting Phase 5b, and again
    before announcing. Returns per-draft results plus totals, with
    `publishable` true only when no draft has an error.

    Note what it cannot see: values a draft puts in prose or a markdown table
    rather than behind a `<!-- field: … -->` marker are reported as
    `coverage` warnings, not as missing. The CiTO step's citation list is the
    known case.
    """
    try:
        return {"ok": True, **_validate_drafts(directory, live=live)}
    except ApiError as e:
        return _fail(e)


@mcp.tool()
def verify_chain(published_path: str, repo_url: str = "", mode: str = "auto") -> dict:
    """Verify a published FORRT chain. Run this before announcing it anywhere.

    Point it at a `nanopubs/PUBLISHED.md` ledger (or the directory holding one).
    Read-only: it never edits, retracts or supersedes — a failing row is for a
    human to act on. `green` is true only when nothing failed.

    What it checks:
      - every required step (01-06) has a URI in the ledger;
      - every URI is really published — present in the constellation, or, for
        the upstream anchors the walk does not reach, served as RDF by the
        `w3id.org/np/` resolver;
      - the Outcome's repository resolves (a Zenodo **version** DOI is the
        expected value — it pins the archived state, where a GitHub URL would
        be a moving target);
      - every DOI the chain cites resolves;
      - **the CiTO relation agrees with the Outcome's verdict** — Validated
        implies confirms, PartiallySupported implies qualifies, Contradicted
        implies disputes. A mismatch means the Outcome and the Citation
        disagree about what the replication found, which is the failure most
        worth catching before anyone reads the chain.

    A step reported as "not enumerated by the walk but its TriG resolves" is
    fine, not a warning: the constellation legitimately stops short of Quote,
    AIDA and Claim.

    `mode` — `auto` (default), `replication`, `reproduction` or `new_research`.
    It changes only what is REQUIRED. Research that starts from scratch has no
    existing work to cite, so no CiTO step and no cited DOI are expected, and
    `auto` infers that from the absence of a published step 06. Everything else
    is checked identically in all three. Reproduction and replication verify the
    same way; pass one explicitly only to make the wording match your study.
    """
    try:
        return {"ok": True, **_verify_chain(published_path, repo_url, mode)}
    except ApiError as e:
        return _fail(e)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
