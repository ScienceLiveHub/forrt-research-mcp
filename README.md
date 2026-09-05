# forrt-research-mcp

An MCP server for **producing** verifiable FORRT nanopublication chains — the
tools a researcher needs while doing the work, not while looking for it. It
serves all three shapes of study on the same rails: reproduction, replication,
and research that starts from scratch.

It is the third of three servers that compose:

| Server | Question it answers |
|---|---|
| [OpenAIRE MCP](https://github.com/ScienceLiveHub/replication-radar/blob/main/docs/openaire-mcp.md) | What is in the literature? |
| [`replication-radar`](https://github.com/ScienceLiveHub/replication-radar) | What is worth replicating, and has it been done? |
| **`forrt-research-mcp`** | **How do I produce a chain that is correct and verifiable?** |

Built for the workflow in
[`forrt-replication-template`](https://github.com/ScienceLiveHub/forrt-replication-template),
but it needs nothing from that repo — any agent can call it.

## Scope: reproduction, replication, and research from scratch

All three work here, and they use the same chain:

```
anchor → AIDA → Claim → Study → Outcome → CiTO
```

**Step 01 is one of three anchors, and they are not variants of one form.**
Which anchor fits is independent of whether the study is a reproduction, a
replication or original research:

| Anchor | Required fields | Notes |
|---|---|---|
| `01_quote` Quote-with-comment | `paper`, `quotation`, `comment` | no type, no label. `quotation` is capped at 500 characters and `comment` at 800 — in the template's own regex, not in prose |
| `01_pico` PICO question | `label`, `description`, `type`, + P/I/C/O descriptions | the only anchor with a question-type vocabulary (5 terms) |
| `01_pcc` PCC question | `label`, `description`, + P/C/C descriptions | **no `type` field at all** |

The three share **no field ids whatsoever**, so call `template_fields` on the
anchor you are actually using rather than assuming they match. The
`pico_question_type` vocabulary is named for PICO deliberately: a PCC question
has no type to look up.

`verify_quote` applies to the Quote anchor only — and that is the one anchor
with a dedicated tool, because a quotation is the one field whose correctness
can be *proved* rather than reviewed.

For a study starting from scratch, the Claim is **your own hypothesis**, derived
from your own question or from the work you are building on. The FORRT Claim
template's `source` is optional, so it needs no external paper — and the
Claim-before-Study order then reads as
**pre-registration**, not as a mismatch. `verify_chain` takes a `mode`
(`auto` / `replication` / `reproduction` / `new_research`) that changes only what
is *required*: from-scratch research has no existing work to cite, so no CiTO
step and no cited DOI are expected.

**The one place the templates still assume an original** is the `study_type`
vocabulary on `04_study`, whose three terms are all replication-flavoured
(Replication Study, Reproduction Study, or both). There is no term for *an
original study testing its own claim*. Three field prompts read oddly too —
`scope` and `methodology` say "is reproduced/replicated", and the Outcome's
`conclusion` says "about the original claim" — but they are only wording;
`validationStatus` (validated / partially supported / contradicted /
inconclusive / not tested) describes testing your own hypothesis perfectly well.

So closing the gap is plausibly **one added vocabulary term and three reworded
prompts**, not a new template family. When that lands, this server picks it up
with no code change: `template_fields` and `vocabulary` fetch live, and
`driftedFromSnapshot` flags the supersession so the vendored copy gets re-cut.

> **A note on using "Reproduction Study" for from-scratch work.** It is a
> reasonable workaround while the vocabulary lacks a better term, but the
> template means the replication-science sense — *"direct reproduction: same
> methodology, same tools"*, i.e. re-running someone else's analysis — not the
> RSE sense of "my work is reproducible". Downstream consumers read it the first
> way: `replication-radar`'s verdict overlay and `verified_claims` will present
> the study as verification of an existing claim.

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

Read `citedPaper`, not the API's top-level `paperDoi` — see *Upstream
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

### `verify_chain(published_path, repo_url="")`

The final check before announcing a chain. Point it at a `nanopubs/PUBLISHED.md`
ledger; `green` is true only when nothing failed. Read-only — it never edits,
retracts or supersedes.

| Check | What it means |
|---|---|
| ledger | every required step (01–06) has a URI |
| reachable | every URI is really published |
| repository | the Outcome's archived version DOI resolves |
| cited-doi | every DOI the chain cites resolves |
| verdict-relation | **the CiTO relation agrees with the Outcome's verdict** |

That last one is the failure most worth catching: `Validated` implies
`confirms`, `PartiallySupported` implies `qualifies`, `Contradicted` implies
`disputes`. A mismatch means the Outcome and the Citation disagree about what
the replication actually found — a chain that cites `confirms` for a paper it
contradicted is worse than no chain at all.

**A constellation alone cannot verify a chain**, which is why this does not try.
On both real chains the walk stops short of the upstream anchors, so anything it
does not enumerate is checked by fetching its TriG from the bare
`w3id.org/np/` resolver — the `/sciencelive/np/` form serves an HTML viewer that
answers 200 and would pass a status-only check while serving no nanopub. A row
reading *"not enumerated by the walk but its TriG resolves"* is a pass, not a
warning.

The Outcome's repository is expected to be a **Zenodo version DOI**, not a
GitHub URL: a version DOI pins the archived state the outcome was computed
from, where `github.com/ORG/REPO` is a moving target. Both are accepted; a DOI
is checked by resolving it, a URL by comparing it to `repo_url`.

Both published chains verify green, matching the hand-run verification recorded
in marine-heatwave's ledger.

### `validate_chain_draft(path)`

Checks `nanopubs/chain-draft.json` — **the artifact that actually gets
published** — before it is handed to the Science Live chain wizard. Run it at the
end of Phase 5b, after `pixi run build-chain-draft` and before pushing the file
and opening the wizard URL.

The markdown drafts are the authoring format; `build_chain_draft.py` turns them
plus `CITATION.cff` and the templates into this file, which the wizard pre-fills
each step from and a human reviews and signs. `validate_draft` checks the input;
this checks the artifact.

What it catches that reading the file cannot:

- a **superseded `template_uri`** — invisible in the JSON, but it makes the
  wizard pre-fill the old form;
- a `prefill` key that is neither a template field nor a known platform
  form-field, which the wizard silently drops;
- a complex field in the wrong shape — `06_citation.st02` must be
  `[{cites, cited}]` with at least one entry, and `04_study.disciplineSelection`
  is a **single object, not an array** (the one asymmetry in the contract);
- a required field neither prefilled nor carried forward;
- a value over the template's own cap, an invalid vocabulary term, a malformed
  date, an unresolved `{{TOKEN}}`, or a DOI that does not resolve;
- a `carry_forward` edge running backwards through the chain.

Fields the wizard fills itself are exempt rather than reported missing:
`02_aida` has no `project`, `03_claim` no `aida`, `04_study` no `claim`.

Both of this project's real chain drafts validate clean, and 13 deliberate
mutations of one are each caught.

### `validate_draft(path)` · `validate_drafts(directory)`

The pre-flight checklist in `docs/forrt-form-fields.md`, actually executed. One
call checks a drafted nanopub end to end: field ids against the live template,
choice values against its enumeration, length caps against its regex, DOIs
against the registry, QIDs against Wikidata. `publishable` is true only when
nothing came back as an error.

This is the composition the other tools exist for — individually they answer
"is this value real?", together they answer "is this draft publishable, and if
not, exactly where is it wrong?"

**Three placeholder conventions, and only one is a problem.** Getting this wrong
made the checker useless on real drafts, so each is handled explicitly:

| In a draft | Meaning | Severity |
|---|---|---|
| `«URI of step 05 …»` | back-reference; the chain wizard fills it from the published step | info |
| `{{ZENODO_VERSION_DOI}}` | release-time token; the release workflow substitutes it | warning |
| anything else standing in for a value | genuinely unfilled | **error** |

A draft whose required fields are nearly all empty is reported once as an
unfilled skeleton — "it has not been drafted yet" — rather than as a wall of
per-field errors.

**What it cannot see.** Values a draft puts in prose or a markdown table rather
than behind a `<!-- field: … -->` marker are reported as `coverage` warnings,
never as missing. The CiTO step's citation list is the known case: `cites` and
`cited` live in a table in both repos checked.

Validated against two real published chains (marine-heatwave and Sado estuary):
all six published steps pass in both, with the only remaining flags being an
unpublished step 07 and the release tokens above.

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
   `citedPaper` derives the paper from the CiTO and sets
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

All 225 tests are hermetic and need no network, no API key, and no live
service: the constellation fixtures are real recorded `/np/constellation`
responses, quote tests build minimal PDFs in-process that reproduce the
extraction artifacts deliberately, and the template/DOI/Wikidata tests stub HTTP
with recorded payloads. The suite stays green through an upstream outage.

The bundled template snapshot under `src/forrt_research_mcp/data/` is vendored
from `ScienceLiveHub/forrt-replication-template`, as is `template_spec.py`.
Re-vendor when `template_fields` reports `driftedFromSnapshot`.

## License

MIT — see [LICENSE](LICENSE).
