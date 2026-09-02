"""Projection tests against a real constellation payload.

The fixture is a verbatim `/np/constellation` response for the marine-heatwave
replication chain (fetched 2026-09-02 from api-dev). It is kept whole, quirks
included, because every behaviour below exists to survive a quirk that showed
up in real data rather than one imagined at design time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forrt_research_mcp import constellation as C

FIXTURE = Path(__file__).parent / "fixtures" / "constellation-mhw.json"

# The paper the chain actually replicates (Oliver et al. 2018, Nat Commun).
OLIVER = "https://doi.org/10.1038/s41467-018-03732-9"
# The paper top-level `paperDoi` wrongly reports (LifeWatch ERIC, RIO).
LIFEWATCH = "https://doi.org/10.3897/rio.10.e119943"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


def test_fixture_still_has_the_shape_these_tests_assume(raw):
    assert raw["nodeCount"] == 98 and raw["edgeCount"] == 1012
    assert raw["paperDoi"] == LIFEWATCH
    assert raw["apexCito"]["citedTargets"] == [OLIVER]


def test_summary_is_a_small_fraction_of_the_raw_payload(raw):
    """The whole point of the projection: a depth-5 walk is not agent-sized."""
    before = len(json.dumps(raw))
    after = len(json.dumps(C.summary("x", raw=raw)))
    assert before > 300_000
    assert after < before * 0.10, f"projection kept {100 * after / before:.1f}% of the payload"


def test_summary_drops_the_bulk_graph_but_reports_what_it_dropped(raw):
    view = C.summary("x", raw=raw)
    assert "nodes" not in view and "edges" not in view
    assert view["neighbourhood"]["nodeCount"] == 98
    assert view["neighbourhood"]["edgeCount"] == 1012


class TestReplicatedPaper:
    """Top-level `paperDoi` is a frequency vote and can name a neighbour's paper.

    Here 4 unrelated Quote nanopubs cite LifeWatch while the chain's own CiTO
    cites Oliver et al. The CiTO is the citation, so the CiTO wins.
    """

    def test_prefers_the_cito_over_the_reported_paper_doi(self, raw):
        paper = C.replicated_paper(raw)
        assert paper["doi"] == OLIVER
        assert paper["source"] == "cito-citedTargets"

    def test_flags_the_disagreement_rather_than_hiding_it(self, raw):
        paper = C.replicated_paper(raw)
        assert paper["disagreesWithReported"] is True
        assert paper["reportedPaperDoi"] == LIFEWATCH

    def test_falls_back_to_paper_doi_when_no_cito_is_reachable(self):
        paper = C.replicated_paper({"paperDoi": LIFEWATCH, "nodes": [], "chains": []})
        assert paper["doi"] == LIFEWATCH
        assert paper["source"] == "paperDoi-fallback"
        assert paper["disagreesWithReported"] is False

    def test_reports_unknown_when_there_is_nothing_to_go_on(self):
        assert C.replicated_paper({})["source"] == "unknown"


class TestUpstreamAnchors:
    def test_foreign_quotes_are_excluded(self, raw):
        """All 4 quote nodes cite LifeWatch, not the replicated paper."""
        assert sum(1 for n in raw["nodes"] if n["stepKind"] == "quote") == 4
        assert C.summary("x", raw=raw)["upstream"] == []

    def test_quotes_citing_the_replicated_paper_are_kept(self, raw):
        """Re-attribute the fixture's quotes to Oliver and they must survive."""
        patched = json.loads(json.dumps(raw))
        for node in patched["nodes"]:
            if node["stepKind"] == "quote":
                node["quote"]["citedDoi"] = OLIVER
        assert len(C.summary("x", raw=patched)["upstream"]) == 4


class TestQuoteTextRecovery:
    """`quote.quotedText` is empty on the current template; the text survives in
    `label` and in two untagged excerpts (the quotation and the comment) whose
    order is not guaranteed."""

    def _a_quote_node(self, raw):
        return next(n for n in raw["nodes"] if n["stepKind"] == "quote")

    def test_the_quirk_this_guards_against_is_still_present(self, raw):
        node = self._a_quote_node(raw)
        assert node["quote"]["quotedText"] == ""
        assert len([e for e in node["plainTextExcerpts"] if len(e) > 40]) >= 2

    def test_recovers_the_quotation_not_the_comment(self, raw):
        node = self._a_quote_node(raw)
        text, comment, source = C._quote_text_and_comment(node)
        assert source == "excerpt-matched-to-label"
        # The label echoes the quotation, so the recovered text must match it.
        assert text.lower().startswith(C._label_stem(node["label"])[:40].lower())
        assert comment and comment != text

    def test_structured_field_wins_when_populated(self):
        node = {"quote": {"quotedText": "real text", "comment": "c"},
                "label": "Paper annotation: something else",
                "plainTextExcerpts": ["x" * 80]}
        assert C._quote_text_and_comment(node) == ("real text", "c", "structured")

    def test_does_not_guess_an_excerpt_when_the_label_matches_none(self):
        node = {"quote": {}, "label": "Paper annotation: totally unrelated stem here",
                "plainTextExcerpts": ["y" * 80, "z" * 80]}
        text, comment, source = C._quote_text_and_comment(node)
        assert source == "label-fallback"
        assert text == "totally unrelated stem here" and comment == ""


class TestChains:
    def test_steps_are_returned_in_forrt_chain_order(self, raw):
        for chain in C.summary("x", raw=raw)["chains"]:
            present = [s for s in chain["stepsPresent"] if s in C.CHAIN_ORDER]
            assert present == sorted(present, key=C.CHAIN_ORDER.index)

    def test_a_hoisted_cito_leaves_the_chain_without_a_cito_step(self, raw):
        """Not a chain-integrity failure — the apex CiTO is moved to top level."""
        view = C.summary("x", raw=raw)
        assert view["chains"][0]["stepsPresent"] == ["Claim", "Study", "Outcome"]
        assert view["apexCito"]["uri"].endswith("9c_Axx-8eCj0x4")
        assert view["apexCito"]["relations"] == ["confirms"]

    def test_verdict_and_confidence_are_surfaced(self, raw):
        chain = C.summary("x", raw=raw)["chains"][0]
        assert chain["verdict"] == "Validated"
        assert chain["confidence"] == "HighConfidence"


class TestPriorWork:
    def test_reports_the_verdict_and_the_authors_own_limitations(self, raw):
        prior = C.prior_work("x", raw=raw)
        assert prior["replicationCount"] == 1
        assert prior["verdicts"] == ["Validated"]
        entry = prior["priorWork"][0]
        assert entry["claimType"] == "descriptive_pattern"
        # The limitations field is where a follow-up study finds what is untested.
        assert "NOT COVER" in entry["limitations"].upper()
        assert entry["repository"].startswith("https://doi.org/10.5281/zenodo")

    def test_carries_the_corrected_paper_identity(self, raw):
        assert C.prior_work("x", raw=raw)["replicatedPaper"]["doi"] == OLIVER
