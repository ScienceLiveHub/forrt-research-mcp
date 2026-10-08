"""Published-chain verification.

Hermetic: the constellation, the resolver and the DOI registry are stubbed.
The shapes come from the two real chains — including the two that made a
first version report failures on chains that were correctly published.
"""
from __future__ import annotations

import pytest

from forrt_research_mcp import chain as C
from forrt_research_mcp.api import ApiError

OUTCOME = "https://w3id.org/sciencelive/np/RAoutcome00000000000000000000000000"
CITO = "https://w3id.org/sciencelive/np/RAcito000000000000000000000000000000"
QUOTE = "https://w3id.org/sciencelive/np/RAquote00000000000000000000000000000"
AIDA = "https://w3id.org/sciencelive/np/RAaida000000000000000000000000000000"
CLAIM = "https://w3id.org/sciencelive/np/RAclaim00000000000000000000000000000"
STUDY = "https://w3id.org/sciencelive/np/RAstudy00000000000000000000000000000"
PAPER = "https://doi.org/10.1038/s41467-018-03732-9"
ARCHIVE = "https://doi.org/10.5281/zenodo.21950033"


def ledger(**overrides) -> str:
    rows = {"01": QUOTE, "02": AIDA, "03": CLAIM,
            "04": STUDY, "05": OUTCOME, "06": CITO}
    rows.update(overrides)
    body = "\n".join(f"| {s} | Template name | {u} | 2026-08-15 |"
                     for s, u in sorted(rows.items()) if u)
    return f"# Published\n\n| Step | Template | URI | Published |\n|---|---|---|---|\n{body}\n"


def view(*, verdict="Validated", relations=("confirms",), repository=ARCHIVE,
         enumerate_upstream=False, apex=True) -> dict:
    steps = [
        {"step": "Claim", "uri": CLAIM},
        {"step": "Study", "uri": STUDY},
        {"step": "Outcome", "uri": OUTCOME, "repository": repository},
    ]
    if not apex:
        steps.append({"step": "CiTO", "uri": CITO,
                      "relations": list(relations), "targets": [PAPER]})
    return {
        "entry": CITO,
        "citedPaper": {"doi": PAPER, "allCitedTargets": [PAPER],
                            "source": "cito-citedTargets"},
        "chains": [{"id": "c1", "outcomeUri": OUTCOME, "verdict": verdict,
                    "confidence": "HighConfidence",
                    "citoRelations": [] if apex else list(relations),
                    "steps": steps, "stepsPresent": [s["step"] for s in steps]}],
        "upstream": ([{"step": "Quote", "uri": QUOTE}] if enumerate_upstream else []),
        "apexCito": ({"uri": CITO, "relations": list(relations),
                      "citedTargets": [PAPER]} if apex else None),
        "researchSynthesis": None,
    }


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(C, "_summary", lambda uri, **kw: view())
    monkeypatch.setattr(C, "fetch_trig", lambda uri, **kw: "@prefix this: <x> .")
    monkeypatch.setattr(C, "resolve_doi", lambda doi: {
        "doi": doi.replace("https://doi.org/", ""), "resolves": True,
        "status": 200, "title": "A resolvable thing"})


def write(tmp_path, text, name="PUBLISHED.md"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


class TestLedgerParsing:
    def test_every_published_row_is_read(self):
        found = C.parse_published(ledger())
        assert sorted(found) == ["01", "02", "03", "04", "05", "06"]

    def test_rows_without_a_uri_are_skipped_not_misparsed(self):
        text = ledger().replace(f"| 06 | Template name | {CITO} |",
                                "| 06 | Template name | _not yet published_ |")
        assert "06" not in C.parse_published(text)

    def test_a_ledger_with_no_uris_says_what_it_expected(self, tmp_path):
        with pytest.raises(ApiError, match="no published URIs"):
            C.verify_chain(write(tmp_path, "# Published\n\nnothing yet\n"))

    def test_a_directory_finds_the_ledger_inside_it(self, tmp_path):
        write(tmp_path, ledger())
        assert C.verify_chain(str(tmp_path))["green"] is True


class TestReachability:
    def test_a_complete_chain_is_green(self, tmp_path):
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is True

    def test_upstream_steps_the_walk_misses_are_verified_by_trig(self, tmp_path):
        """The case that would make a constellation-only check fail both real
        chains: Quote/AIDA/Claim are published but not enumerated."""
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is True
        fallback = [r for r in result["rows"]
                    if r["check"] == "reachable" and "not enumerated" in r["message"]]
        assert {r["step"] for r in fallback} == {"01", "02"}

    def test_a_uri_that_resolves_nowhere_fails(self, tmp_path, monkeypatch):
        def boom(uri, **kw):
            raise ApiError("served the HTML viewer, not a nanopub")
        monkeypatch.setattr(C, "fetch_trig", boom)
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is False
        assert any(r["check"] == "reachable" and r["status"] == "fail"
                   for r in result["rows"])

    def test_a_missing_required_step_fails(self, tmp_path):
        text = ledger().replace(f"| 03 | Template name | {CLAIM} | 2026-08-15 |\n", "")
        result = C.verify_chain(write(tmp_path, text))
        assert result["green"] is False
        assert any(r["check"] == "ledger" and r.get("step") == "03"
                   for r in result["rows"])


class TestRepository:
    """The Outcome pins a Zenodo *version* DOI, not a GitHub URL — deliberately,
    since `github.com/ORG/REPO` names a moving target. Comparing it to a git
    remote made a first version fail a correctly published chain."""

    def test_an_archived_version_doi_that_resolves_passes(self, tmp_path):
        rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
        repo = [r for r in rows if r["check"] == "repository"][0]
        assert repo["status"] == "pass"
        assert "archived version DOI" in repo["message"]

    def test_a_repository_doi_that_does_not_resolve_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False, "status": 404})
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False

    def test_a_github_url_is_compared_against_the_given_remote(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            repository="https://github.com/ScienceLiveHub/mhw-replication"))
        ok = C.verify_chain(write(tmp_path, ledger()),
                            "https://github.com/ScienceLiveHub/mhw-replication.git")
        assert ok["green"] is True

    def test_a_github_url_naming_a_different_repo_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            repository="https://github.com/someone/else"))
        bad = C.verify_chain(write(tmp_path, ledger()),
                             "https://github.com/ScienceLiveHub/mhw-replication")
        assert bad["green"] is False

    def test_an_outcome_declaring_no_repository_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(repository=""))
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False


class TestVerdictAgreesWithCitation:
    """The failure most worth catching: a chain that cites `confirms` for an
    outcome that actually contradicted the paper."""

    @pytest.mark.parametrize("verdict,relation", [
        ("Validated", "confirms"),
        ("PartiallySupported", "qualifies"),
        ("Contradicted", "disputes"),
    ])
    def test_agreement_passes(self, tmp_path, monkeypatch, verdict, relation):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            verdict=verdict, relations=(relation,)))
        rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
        assert [r for r in rows if r["check"] == "verdict-relation"][0]["status"] == "pass"

    def test_a_contradicted_outcome_cited_as_confirms_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            verdict="Contradicted", relations=("confirms",)))
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is False
        bad = [r for r in result["rows"] if r["check"] == "verdict-relation"][0]
        assert "disagree about what this replication found" in bad["message"]

    def test_a_hoisted_apex_cito_still_gets_checked(self, tmp_path, monkeypatch):
        """When the CiTO is at the apex its relations leave chains[], and a
        first version silently skipped the check for the whole chain."""
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(apex=True))
        rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
        assert [r for r in rows if r["check"] == "verdict-relation"]

    def test_an_in_chain_cito_is_checked_too(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(apex=False))
        rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
        assert [r for r in rows if r["check"] == "verdict-relation"][0]["status"] == "pass"


class TestCitedDois:
    def test_a_resolving_cited_doi_passes(self, tmp_path):
        rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
        assert [r for r in rows if r["check"] == "cited-target"][0]["status"] == "pass"

    def test_an_unregistered_cited_doi_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False,
            "status": 404 if "s41467" in doi else 200, "title": "x"})
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is False

    def test_a_registry_outage_warns_rather_than_failing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False, "status": 503})
        result = C.verify_chain(write(tmp_path, ledger()))
        assert any(r["status"] == "warn" for r in result["rows"])

    def test_a_chain_citing_nothing_fails(self, tmp_path, monkeypatch):
        empty = view()
        empty["citedPaper"] = {"doi": "", "allCitedTargets": [],
                                    "source": "unknown"}
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: empty)
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False


def test_failures_are_reported_before_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
        verdict="Contradicted", relations=("confirms",)))
    rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
    assert rows[0]["status"] == "fail"


class TestNonVerdictCitations:
    """A from-scratch study cites a method paper or data source, not an original
    it agrees or disagrees with. Cross-checking the verdict against such a
    relation would report a failure on a perfectly correct chain."""

    @pytest.mark.parametrize("relation", [
        "citesAsAuthority", "citesAsDataSource", "usesMethodIn",
        "obtainsBackgroundFrom", "extends",
    ])
    def test_a_non_verdict_relation_is_not_a_disagreement(
            self, tmp_path, monkeypatch, relation):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            verdict="Validated", relations=(relation,)))
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is True
        row = [r for r in result["rows"] if r["check"] == "verdict-relation"][0]
        assert row["status"] == "info"
        assert "asserts no verdict" in row["message"]

    def test_a_wrong_verdict_relation_still_fails(self, tmp_path, monkeypatch):
        """The check must keep its teeth where it applies."""
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            verdict="Contradicted", relations=("confirms",)))
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False

    def test_a_mixed_citation_is_still_cross_checked(self, tmp_path, monkeypatch):
        """Citing both a data source AND a verdict relation: the verdict one
        still has to agree."""
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            verdict="Contradicted", relations=("citesAsDataSource", "confirms")))
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False


class TestModes:
    """Reproduction, replication and from-scratch research all verify here.
    Only what is REQUIRED differs: a study that starts from scratch has no
    existing work to cite."""

    def test_a_from_scratch_chain_without_a_cito_is_green(self, tmp_path, monkeypatch):
        no_citation = view()
        no_citation["citedPaper"] = {"doi": "", "allCitedTargets": [],
                                     "source": "unknown"}
        no_citation["apexCito"] = None
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: no_citation)
        text = ledger().replace(f"| 06 | Template name | {CITO} | 2026-08-15 |\n", "")
        result = C.verify_chain(write(tmp_path, text))
        assert result["mode"] == "new_research"
        assert result["green"] is True

    def test_the_same_chain_fails_when_declared_a_replication(self, tmp_path, monkeypatch):
        """A replication really does need something to cite."""
        no_citation = view()
        no_citation["citedPaper"] = {"doi": "", "allCitedTargets": [],
                                     "source": "unknown"}
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: no_citation)
        text = ledger().replace(f"| 06 | Template name | {CITO} | 2026-08-15 |\n", "")
        result = C.verify_chain(write(tmp_path, text), mode="replication")
        assert result["green"] is False
        assert any("mode='new_research'" in r["message"] for r in result["rows"])

    def test_auto_infers_replication_when_a_cito_is_published(self, tmp_path):
        assert C.verify_chain(write(tmp_path, ledger()))["mode"] == "replication"

    @pytest.mark.parametrize("mode", ["replication", "reproduction"])
    def test_reproduction_and_replication_verify_identically(self, tmp_path, mode):
        """The difference lives in the Study's `type`, which the constellation
        does not expose — so verification cannot and should not distinguish."""
        result = C.verify_chain(write(tmp_path, ledger()), mode=mode)
        assert result["green"] is True
        assert result["mode"] == mode

    def test_an_unknown_mode_lists_the_real_ones(self, tmp_path):
        with pytest.raises(ApiError, match="expected one of"):
            C.verify_chain(write(tmp_path, ledger()), mode="primary")


class TestNonDoiCitationTargets:
    """A CiTO may cite a software repository by URL, not only a DOI. Treating
    every target as a DOI reported a correctly published chain as broken — one
    of this project's real question-rooted chains credits
    github.com/GRID4EARTH/healpix-analyse."""

    def test_a_reachable_repository_url_passes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: {
            **view(), "citedPaper": {
                "doi": "https://github.com/GRID4EARTH/healpix-analyse",
                "allCitedTargets": ["https://github.com/GRID4EARTH/healpix-analyse"],
                "source": "cito-citedTargets"}})
        monkeypatch.setattr(C, "_url_reachable", lambda url, **kw: True)
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is True
        row = [r for r in result["rows"] if r["check"] == "cited-target"][0]
        assert row["status"] == "pass" and "non-DOI target" in row["message"]

    def test_a_dead_repository_url_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: {
            **view(), "citedPaper": {
                "doi": "https://github.com/nope/gone",
                "allCitedTargets": ["https://github.com/nope/gone"],
                "source": "cito-citedTargets"}})
        monkeypatch.setattr(C, "_url_reachable", lambda url, **kw: False)
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False

    def test_an_unreachable_host_warns_rather_than_failing(self, tmp_path, monkeypatch):
        def boom(url, **kw):
            raise ApiError("connection refused")
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: {
            **view(), "citedPaper": {
                "doi": "https://example.invalid/x",
                "allCitedTargets": ["https://example.invalid/x"],
                "source": "cito-citedTargets"}})
        monkeypatch.setattr(C, "_url_reachable", boom)
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is True
        assert any(r["check"] == "cited-target" and r["status"] == "warn"
                   for r in result["rows"])

    def test_a_target_that_is_neither_doi_nor_url_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: {
            **view(), "citedPaper": {
                "doi": "some-free-text", "allCitedTargets": ["some-free-text"],
                "source": "cito-citedTargets"}})
        assert C.verify_chain(write(tmp_path, ledger()))["green"] is False


class TestLedgerLayoutVariance:
    """Ledgers vary. `pangeo-fish-replication` lists step 07 in the numbered
    table and the rest in a second table keyed by description ("PCC question:
    …", "FORRT Replication Study: …"). A step-number parser sees one row and
    would report five steps missing — a false verdict about a chain that IS
    published."""

    def stray_ledger(self) -> str:
        return (f"| 07 | Research Software | {STUDY} | 2026-06-05 |\n\n"
                "## Individual nanopublications\n\n"
                f"| PCC question | `{QUOTE}` |\n"
                f"| FORRT Replication Study | `{CLAIM}` |\n"
                f"| FORRT Replication Outcome | `{OUTCOME}` |\n")

    def test_uris_outside_the_step_table_are_counted(self):
        text = self.stray_ledger()
        parsed = C.parse_published(text)
        assert list(parsed) == ["07"]
        assert len(C.unparsed_uris(text, parsed)) == 3

    def test_a_uri_already_claimed_is_not_double_counted(self):
        text = ledger()
        assert C.unparsed_uris(text, C.parse_published(text)) == []

    def test_the_two_uri_forms_are_reconciled(self):
        """The same nanopub written both ways must not look like two."""
        text = f"| 05 | Outcome | {OUTCOME} | 2026 |\n\nAlso: {OUTCOME.replace('/sciencelive/np/', '/np/')}\n"
        assert C.unparsed_uris(text, C.parse_published(text)) == []

    def test_the_report_says_it_cannot_read_the_layout(self, tmp_path):
        result = C.verify_chain(write(tmp_path, self.stray_ledger()))
        ledger_rows = [r for r in result["rows"] if r["check"] == "ledger"
                       and r["status"] == "fail"]
        assert len(ledger_rows) == 1, "one layout complaint, not one per step"
        assert "cannot map to step numbers" in ledger_rows[0]["message"]
        assert ledger_rows[0]["unparsedUris"]

    def test_a_genuinely_incomplete_ledger_still_names_its_steps(self, tmp_path):
        """No stray URIs means the steps really are missing, and the report
        should say which."""
        text = ledger().replace(f"| 03 | Template name | {CLAIM} | 2026-08-15 |\n", "")
        result = C.verify_chain(write(tmp_path, text))
        assert any(r["check"] == "ledger" and r.get("step") == "03"
                   for r in result["rows"])


# --- ledger layouts written by hand ---------------------------------------
#
# Real ledgers vary. These are the three forms the URI cell has actually taken
# in study repositories; all three name the same nanopub and all three must
# parse. The markdown-link form is the one that broke `verify_chain` on
# annefou/extreme-precipitation-climatedt-replication: it reported "no published
# URIs found" for a chain whose six steps were all live.

_URI_05 = "https://w3id.org/sciencelive/np/RACgxEzHoCa6VWoEV7tFPLMAhqrwXMvUlzg3KuWwHl0l4"


def _ledger(cell: str) -> str:
    return (
        "| Step | Template | URI | Published |\n"
        "|---|---|---|---|\n"
        f"| 05 | FORRT Replication Outcome | {cell} | 2026-09-07 |\n"
    )


def test_parse_published_accepts_a_bare_uri():
    assert C.parse_published(_ledger(_URI_05)) == {"05": _URI_05}


def test_parse_published_accepts_a_backticked_uri():
    assert C.parse_published(_ledger(f"`{_URI_05}`")) == {"05": _URI_05}


def test_parse_published_accepts_a_markdown_link():
    cell = f"[`RACgxEzHoCa6…`]({_URI_05})"
    assert C.parse_published(_ledger(cell)) == {"05": _URI_05}


def test_parse_published_still_skips_an_unpublished_row():
    assert C.parse_published(_ledger("_not yet published_")) == {}


def test_parse_published_ignores_a_non_nanopub_url():
    assert C.parse_published(_ledger("https://example.org/not-a-nanopub")) == {}


# --- A network of chains (forrt-research-mcp#11) --------------------------
#
# Published work is not one chain per walk. The real case: the
# fiesta-decrop-reproduction CiTO `confirms` Decrop 2025, and the
# fiesta-scattering-bio chain `extends` the same paper, so a walk from either
# reaches both. The ledger's own CiTO is hoisted to the apex, and with two
# chains in view a first version skipped the ledger's check and reported the
# neighbour's `extends` instead — GREEN whatever the ledger's CiTO said.

NEIGHBOUR_OUTCOME = "https://w3id.org/sciencelive/np/RAneighbouroutcome0000000000000000"
METHOD = "https://doi.org/10.1051/0004-6361/202244566"
DATA = "https://doi.org/10.5281/zenodo.19701133"


def cito_trig(pairs, citing=OUTCOME) -> str:
    """A CiTO nanopub as the platform publishes it, with the given pairs."""
    body = ";\n    ".join(f"<http://purl.org/spar/cito/{rel}> <{target}>"
                          for rel, target in pairs)
    cito = CITO.replace("/sciencelive/np/", "/np/")
    return f"""@prefix np: <http://www.nanopub.org/nschema#> .
<{cito}/Head> {{
  <{cito}> a np:Nanopublication; np:hasAssertion <{cito}/assertion> .
}}
<{cito}/assertion> {{
  <{citing}> a <https://schema.org/CreativeWork>;
    {body} .
}}
"""


def quote_trig(paper=PAPER) -> str:
    quote = QUOTE.replace("/sciencelive/np/", "/np/")
    return f"""@prefix np: <http://www.nanopub.org/nschema#> .
<{quote}/Head> {{
  <{quote}> a np:Nanopublication; np:hasAssertion <{quote}/assertion> .
}}
<{quote}/assertion> {{
  <{quote}#q> <http://purl.org/spar/cito/quotes> <{paper}> .
}}
"""


def network_view(neighbour_relations=("extends",)) -> dict:
    """The ledger's chain plus a neighbour citing the same paper, own CiTO at the apex."""
    v = view(apex=True)
    v["chains"].append({
        "id": "c2", "outcomeUri": NEIGHBOUR_OUTCOME, "verdict": "Validated",
        "confidence": "HighConfidence", "citoRelations": list(neighbour_relations),
        "steps": [{"step": "Outcome", "uri": NEIGHBOUR_OUTCOME}], "stepsPresent": ["Outcome"]})
    return v


def stub_trig(monkeypatch, cito=None, quote=None):
    def fetch(uri, **kw):
        if C.canonical_uri(uri) == C.canonical_uri(CITO) and cito is not None:
            return cito
        if C.canonical_uri(uri) == C.canonical_uri(QUOTE) and quote is not None:
            return quote
        return "@prefix this: <x> ."
    monkeypatch.setattr(C, "fetch_trig", fetch)


def verdict_rows(result):
    return [r for r in result["rows"] if r["check"] == "verdict-relation"]


class TestReadCito:
    def test_pairs_each_relation_with_its_target(self):
        got = C.read_cito(cito_trig([("confirms", PAPER), ("usesMethodIn", METHOD)]))
        assert got["citing"] == [OUTCOME]
        assert sorted(got["pairs"]) == sorted([("confirms", PAPER), ("usesMethodIn", METHOD)])

    def test_reads_the_quoted_paper(self):
        assert C.read_quoted_paper(quote_trig()) == PAPER

    def test_a_question_anchor_quotes_nothing(self):
        assert C.read_quoted_paper("@prefix this: <x> .") == ""


class TestNetworkOfChains:
    def test_the_ledgers_own_cito_is_judged_not_the_neighbours(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: network_view())
        stub_trig(monkeypatch, cito=cito_trig([("confirms", PAPER)]), quote=quote_trig())
        result = C.verify_chain(write(tmp_path, ledger()))
        rows = verdict_rows(result)
        assert [r["status"] for r in rows] == ["pass"]
        assert "confirms" in rows[0]["message"]

    def test_a_disagreeing_cito_fails_even_with_a_neighbour_in_view(self, tmp_path, monkeypatch):
        """The case the first version passed: GREEN with `disputes` on a Validated outcome."""
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: network_view())
        stub_trig(monkeypatch, cito=cito_trig([("disputes", PAPER)]), quote=quote_trig())
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is False
        assert verdict_rows(result)[0]["status"] == "fail"

    def test_neighbours_are_context_not_verdicts(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: network_view(("disputes",)))
        stub_trig(monkeypatch, cito=cito_trig([("confirms", PAPER)]), quote=quote_trig())
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is True
        context = [r for r in result["rows"] if r["check"] == "neighbours"]
        assert context and context[0]["status"] == "info"
        assert NEIGHBOUR_OUTCOME in context[0]["outcomes"]

    def test_an_unreadable_cito_in_a_network_warns_instead_of_skipping(
            self, tmp_path, monkeypatch):
        """No TriG and the apex is someone else's CiTO: say so, never pass silently."""
        v = network_view()
        v["apexCito"] = {"uri": "https://w3id.org/np/RAsomeoneelsescito000000000000000",
                         "relations": ["extends"], "citedTargets": [PAPER]}
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: v)
        stub_trig(monkeypatch)
        rows = verdict_rows(C.verify_chain(write(tmp_path, ledger())))
        assert [r["status"] for r in rows] == ["warn"]

    def test_a_cito_citing_from_another_outcome_is_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: network_view())
        stub_trig(monkeypatch, cito=cito_trig([("confirms", PAPER)], citing=NEIGHBOUR_OUTCOME),
                  quote=quote_trig())
        rows = C.verify_chain(write(tmp_path, ledger()))["rows"]
        assert any(r["check"] == "cito-citing-work" and r["status"] == "warn" for r in rows)


class TestRelationOnTheQuotedPaper:
    """One CiTO cites several works; only the relation on the claim's paper
    carries the verdict."""

    def test_a_confirms_on_the_method_paper_does_not_hide_a_wrong_relation(
            self, tmp_path, monkeypatch):
        stub_trig(monkeypatch, quote=quote_trig(),
                  cito=cito_trig([("disputes", PAPER), ("confirms", METHOD)]))
        result = C.verify_chain(write(tmp_path, ledger()))
        assert verdict_rows(result)[0]["status"] == "fail"

    def test_extends_on_the_paper_with_method_and_data_is_info(self, tmp_path, monkeypatch):
        """fiesta-scattering-bio: extends the paper, uses a method, cites data."""
        stub_trig(monkeypatch, quote=quote_trig(), cito=cito_trig(
            [("extends", PAPER), ("usesMethodIn", METHOD), ("citesAsDataSource", DATA)]))
        result = C.verify_chain(write(tmp_path, ledger()))
        assert result["green"] is True
        assert verdict_rows(result)[0]["status"] == "info"

    def test_a_cito_that_never_cites_the_quoted_paper_warns(self, tmp_path, monkeypatch):
        stub_trig(monkeypatch, quote=quote_trig(), cito=cito_trig([("confirms", METHOD)]))
        assert verdict_rows(C.verify_chain(write(tmp_path, ledger())))[0]["status"] == "warn"


class TestCitedPaperFromOwnCito:
    """forrt-research-mcp#9: the paper is the verdict relation's target, not
    the alphabetically first of the CiTO's targets."""

    def test_the_quoted_paper_with_the_verdict_relation_wins(self, tmp_path, monkeypatch):
        portal = "https://colombia.mapbiomas.org/"
        stub_trig(monkeypatch, quote=quote_trig(), cito=cito_trig(
            [("citesAsDataSource", portal), ("qualifies", PAPER), ("usesMethodIn", METHOD)]))
        monkeypatch.setattr(C, "_summary", lambda uri, **kw: view(
            verdict="PartiallySupported", relations=("qualifies",)))
        monkeypatch.setattr(C, "_url_reachable", lambda url, **kw: True)
        paper = C.verify_chain(write(tmp_path, ledger()))["citedPaper"]
        assert paper["doi"] == PAPER
        assert paper["selectedBy"] == "verdict-relation-on-quoted-paper"
        assert paper["source"] == "cito-nanopub"
        assert portal in paper["allCitedTargets"]

    def test_without_a_readable_cito_the_walks_answer_stands(self, tmp_path):
        paper = C.verify_chain(write(tmp_path, ledger()))["citedPaper"]
        assert paper["doi"] == PAPER
        assert paper["source"] == "cito-citedTargets"
