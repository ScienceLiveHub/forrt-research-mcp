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
| Which sentence is the claim | Whether that sentence is in the PDF |
| How to phrase the AIDA | Where it is, and under what SHA-256 |
| Which claim type and CiTO relation apply | What prior chains already claimed |

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

> **The default base is `api-dev` deliberately.** As of 2026-09-02 production
> `/np/constellation` returns HTTP 500 on known-good URIs while `/health`
> reports healthy; `api-dev` serves the same URIs with 200. Point
> `SCIENCELIVE_API_BASE` at production once that is fixed.

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

## Upstream quirks this handles

Found by probing live data rather than reading the API contract, and each one is
pinned by a test against a real recorded payload:

1. **`paperDoi` can name the wrong paper.** It is chosen by frequency across the
   whole walk, so a neighbouring study's Quote nanopubs can outvote the chain's
   own citation. On the marine-heatwave chain it reports the LifeWatch ERIC
   paper (4 unrelated quotes) instead of Oliver et al. 2018, which the chain's
   CiTO actually cites. `replicatedPaper` derives the paper from the CiTO and
   sets `disagreesWithReported` when the two differ.
2. **`quote.quotedText` comes back empty** on nanopubs using the current quote
   template. The text survives in `label` and in two untagged excerpts — the
   quotation and the annotator's comment, in no guaranteed order. We match them
   against the label stem rather than assuming a position, and report the
   recovery in `text_source`.
3. **The apex CiTO is hoisted** out of `chains[].steps[]` to top level.
4. **The `/sciencelive/np/` URI form serves an HTML viewer** that answers 200, so
   a status-only reachability check passes even when no nanopub is served. We
   normalise to the bare `w3id.org/np/` resolver and assert the body is RDF.

## Development

```bash
pip install -e '.[dev]'
pytest
```

Tests are hermetic and need no network: the constellation fixture is a real
recorded `/np/constellation` response, and quote tests build minimal PDFs
in-process that reproduce the extraction artifacts deliberately.

## License

MIT — see [LICENSE](LICENSE).
