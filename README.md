# forrt-research-mcp

An MCP server for **producing** verifiable FORRT nanopublication chains — the
tools a researcher needs while doing the work, not while looking for it. It
serves replications and reproductions today, and is named for where it is going:
starting new research from a research question, on the same rails.

It is the third of three servers that compose:

| Server | Question it answers |
|---|---|
| [OpenAIRE MCP](https://github.com/ScienceLiveHub/replication-radar/blob/main/docs/openaire-mcp.md) | What is in the literature? |
| [`replication-radar`](https://github.com/ScienceLiveHub/replication-radar) | What is worth replicating, and has it been done? |
| **`forrt-research-mcp`** | **How do I produce a chain that is correct and verifiable?** |

Built for the workflow in
[`forrt-replication-template`](https://github.com/ScienceLiveHub/forrt-replication-template),
but it needs nothing from that repo — any agent can call it.

## Scope: what a FORRT chain can express today

The tool names here are deliberately neutral — `constellation`, `prior_work`,
`verify_quote` — because the chain has two entry points, and the current Science
Live templates serve only one of them end to end:

- **Chains that test an existing claim** work fully, whether paper-rooted (Quote)
  or question-rooted (PICO / PCC): anchor → AIDA → Claim → Study → Outcome → CiTO.
- **Genuinely new research** — you have a question, you run a study, you report a
  finding — works for the *first half only*. Question → AIDA → Claim is clean,
  because the FORRT Claim template takes your own `source` URI, so declaring your
  own original claim is exactly what it is for. The second half is not: the
  Replication Study template's fields are `scope` ("what part of the claim is
  reproduced/replicated"), `methodology` ("how the claim is
  reproduced/replicated") and `deviation` ("deviations from original
  methodology"), and the Outcome's `conclusion` is "about the original claim"
  with a `validationStatus` of Validated / PartiallySupported / Contradicted.
  With no original, those fields have no referent.

Closing that needs new templates on the Science Live side — a Research Study /
Research Finding pair — not a workaround in this server. Until then, publish the
Question → AIDA → Claim half and say so, rather than bending replication fields
into a shape they were not made for.

## Why a server and not a prompt

Two jobs here look like reasoning but are not, and doing them in an agent loop
makes them unreproducible and expensive:

**1. Reading a published chain.** `/np/constellation` walks the FORRT citation
graph bidirectionally and returns everything it reaches. For a single
marine-heatwave chain that is **330 KB, 98 nodes and 1012 edges — of which 64
nodes are AIDA statements belonging to entirely different studies**. Handing
that to an agent burns its context and invites it to reason over another paper's
claims. `constellation` returns the same chain in ~16 KB (4.7 %).

**2. Checking a quotation is real.** A FORRT Quote nanopub must be verbatim. That
is a string search, not a judgement — so it should be a tool that cannot be
talked out of its answer, and that anyone can re-run to get the same result.

## What it does not do

It does not search papers (that is the OpenAIRE MCP), rank replication targets
(that is `replication-radar`), or write nanopub content. It also does not
*extract* claims: choosing which sentence carries a paper's headline claim is a
judgement, and wrapping a judgement in a tool would not make it reproducible — a
model behind a tool boundary is exactly as non-deterministic as one in the agent
loop. The division this server is built around:

| The model proposes | This server disposes |
|---|---|
| Which sentence is the claim | Whether that sentence is in the PDF, where, under what SHA-256 |
| How to phrase the AIDA | What fields the template actually has, and their caps |
| Which claim type or CiTO relation applies | Which values the form will actually accept |
| Which Wikidata topic is meant | Whether that QID exists and is of the right type |
| Which paper to cite | Whether that DOI resolves, and to what |
| Whether to extend or dispute prior work | What prior chains already claimed |

## Install

```bash
pipx install forrt-research-mcp
claude mcp add forrt-research -s user -- forrt-research-mcp
```

User-scoped (`-s user`) so it is available in every session and folder, and does
not clash with a per-repo config. For other agents, the stdio command is
`forrt-research-mcp`.

```bash
# optional
export SCIENCELIVE_API_BASE="https://api-dev.sciencelive4all.org"  # default
export SCIENCELIVE_API_KEY="sl_…"   # /np/constellation is a public read
```

> **The default base is `api-dev` deliberately, for now.** `/np/constellation`
> is newer than the current production deployment, so as of 2026-09-02
> production answers HTTP 500 on known-good URIs while `api-dev` serves them
> with 200. This is a deployment lag, not a fault. Once the release reaches
> production, switch `DEFAULT_API_BASE` in `api.py` — `SCIENCELIVE_API_BASE`
> already overrides it in the meantime.

## Tools

### `verify_quote(pdf_path, quotation)`

Proves a candidate quotation is in a source PDF before it is published as
verbatim. Returns a graded verdict with page, character offsets and the file's
SHA-256:

| Verdict | Meaning |
|---|---|
| `exact` | byte-identical to the extracted page text |
| `normalized` | matched after whitespace / ligature / typographic punctuation / line-break-hyphen repair |
| `extraction_tolerant` | additionally ignored hyphens and punctuation spacing |
| `not_found` | **not in this PDF — do not publish it** |

Every tier canonicalises *formatting only* — never words, digits or order. A
quotation with one digit changed scores ~0.91 similarity against the source and
is still `not_found`; on a miss, `closest.text_in_pdf` shows what the paper
actually says there.

A tier below `exact` is normal. PDF extraction inserts line breaks and loses
hyphens: the quotation published in the real marine-heatwave chain matches only
at `extraction_tolerant`, because `pypdf` reads the paper's `35-year` as
`35year` and `(p <` as `( p <`. **"Character-for-character" is not literally
achievable against extracted PDF text**, which is why the result is graded
rather than boolean.

### `constellation(uri, depth=5, max_nodes=80)`

The FORRT chain(s) reachable from a published nanopub URI, projected to
agent-size: chains with their steps in chain order, the apex CiTO, any Research
Synthesis, and the Quote/Question anchors attributable to this paper.

Read `replicatedPaper`, not the API's top-level `paperDoi` — see *Upstream
quirks* below.

`stepsPresent` legitimately omits steps: a CiTO at the apex of a constellation
is hoisted out of its chain, and Quote/AIDA anchors are often not enumerated.
Missing does not mean unpublished.

### `prior_work(uri)`

What has already been claimed about a paper — the starting point for new work,
replication or otherwise. Per completed chain: claim type, `scope`, `method`,
`deviations`, verdict, confidence, repository, and `limitations`.

`limitations` is the field to read most carefully. It is where previous authors
stated, in signed and immutable words, what their study did *not* cover — which
is often exactly where the next study begins. In the marine-heatwave chain it
records that the paper's better-known 54 % headline describes a different
analysis over a different period and was not tested.

### `constellation_raw(uri, …)`

The unprojected response, for debugging the graph or an upstream data problem.
Large; prefer `constellation`.

### `template_fields(step, live=True)` · `vocabulary(name, live=True)` · `list_schemas()`

A nanopub template **is** the schema for its chain step, so these turn "never
invent a field name" and "never invent a claim type" from rules an agent has to
remember into a lookup that can only return real values.

`template_fields` returns the real field ids, prompts, required/repeatable
flags, and the `regex` / `prefix` / `datatype` constraints — which is where the
Quote template's character cap actually lives, rather than in a hand-written
doc that drifts. `step` accepts `05_outcome`, `05`, or `outcome`.

`vocabulary` returns the allowed values of a controlled vocabulary, taken from
the real restricted-choice field:

| Name | From | Terms |
|---|---|---|
| `claim_type` | `03_claim.forrtType` | 7 |
| `study_type` | `04_study.type` | 3 — the Reproduction vs Replication distinction |
| `validation_status` | `05_outcome.validationStatus` | 5 |
| `confidence_level` | `05_outcome.confidenceLevel` | 5 |
| `cito_relation` | `06_citation.cites` | 43, via a separate value-list nanopub |
| `question_type` | `01_pico.type` | 5 |

Both fetch live by default and fall back to a **bundled snapshot** when the
network is unavailable — always reporting which was used, and setting
`driftedFromSnapshot` when the upstream template has been superseded. A drifted
result means the live values win and this package needs re-vendoring; it is a
loud, reviewable event rather than slow silent divergence.

`cito_relation` is the one vocabulary that cannot be resolved offline: it lives
in a separate value-list nanopub. Offline it returns `source:
"unavailable-offline"` with a warning rather than an empty list, because an
empty list reads as "no valid values."

### `resolve_doi(doi)`

Does this DOI resolve, and to what? `resolves: false` means it is not
registered — **a well-formed DOI is not a real one**, and a fabricated one is
indistinguishable from a genuine one until something asks the registry. Returns
the registered title, authors, year and container so you can confirm it is the
paper you meant, not merely a paper that exists. Accepts bare, URL, and `doi:`
forms. A 5xx is reported as transient rather than as a bad DOI.

### `wikidata_lookup(query, expected_type="", limit=5)`

Real Wikidata candidates for a term, with their actual P31/P279 types. Pass
`expected_type` as a QID and each candidate is marked `typeMatches`.

It deliberately **does not choose** — picking the right sense of an ambiguous
label is a judgement. Searching `Bombus` with `Q16521` (taxon) returns the
insect genus as a match and *the album of the same name* as not, which is
exactly the failure worth catching before a QID gets signed into a nanopub.
Candidates are annotated, never filtered: a near miss is often the informative
result. Zero candidates means leave the field empty — never fall back to a QID
from memory.

## Upstream quirks this handles

Found by probing live data rather than reading the API contract, and each one is
pinned by a test against a real recorded payload (two constellations, from
unrelated studies):

1. **Cloudflare rejects urllib's default User-Agent.** The API runs on
   Cloudflare Workers, whose bot protection answers `Python-urllib/3.x` with
   HTTP 403 — same URI, same key, 200 under any normal agent. Every request this
   client makes sets an explicit `User-Agent`. Do the same in your own client;
   no hermetic test can catch it.
2. **`paperDoi` can name the wrong paper.** It is chosen by frequency across the
   whole walk, so a neighbouring study's Quote nanopubs can outvote the chain's
   own citation. On the marine-heatwave chain it reports the LifeWatch ERIC
   paper (4 unrelated quotes) instead of Oliver et al. 2018, which the chain's
   CiTO actually cites — and it names **the same wrong paper** from the
   unrelated Sado-estuary entry point, so this is systemic, not one bad record.
   `replicatedPaper` derives the paper from the CiTO and sets
   `disagreesWithReported` when the two differ.
3. **The walk stops short, and missing does not mean unpublished.** Two real
   chains enumerated `[Claim, Study, Outcome]` and `[Study, Outcome, CiTO]`,
   while the Quote, AIDA and Claim nanopubs listed in those repos'
   `PUBLISHED.md` were published and merely unreachable. `stepsNotEnumerated`
   reports the gap. **A constellation alone cannot verify a complete chain** —
   fetch those URIs' TriG directly.
4. **`quote.quotedText` comes back empty** on nanopubs using the current quote
   template. The text survives in `label` and in two untagged excerpts — the
   quotation and the annotator's comment, in no guaranteed order. We match them
   against the label stem rather than assuming a position, and report the
   recovery in `text_source`.
5. **The apex CiTO is hoisted** out of `chains[].steps[]` to top level.
6. **The `/sciencelive/np/` URI form serves an HTML viewer** that answers 200, so
   a status-only reachability check passes even when no nanopub is served. We
   normalise to the bare `w3id.org/np/` resolver and assert the body is RDF.

## Development

```bash
pip install -e '.[dev]'
pytest
```

All 101 tests are hermetic and need no network, no API key, and no live
service: the constellation fixtures are real recorded `/np/constellation`
responses, quote tests build minimal PDFs in-process that reproduce the
extraction artifacts deliberately, and the template/DOI/Wikidata tests stub HTTP
with recorded payloads. The suite stays green through an upstream outage.

The bundled template snapshot under `src/forrt_research_mcp/data/` is vendored
from `ScienceLiveHub/forrt-replication-template`, as is `template_spec.py`.
Re-vendor when `template_fields` reports `driftedFromSnapshot`.

## License

MIT — see [LICENSE](LICENSE).
