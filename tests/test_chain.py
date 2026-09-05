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
