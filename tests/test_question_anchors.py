"""Question-rooted anchors — the PICO and PCC alternatives to a Quote.

Fixtures are real `/np/constellation` responses for two published question
nanopubs (fetched 2026-09-05 from api-dev):

  PICO  RA3YO8FF…  equal-area HEALPix gridding and latitude bias
  PCC   RAEDNajh…  artificial light intrusion on the Po Delta

Both were published standalone — one node, no edges, no chain built on them yet
— which is itself the case worth pinning: a question anchor exists before the
chain that will grow from it.

A first version returned these as empty. Question nodes carry their content in a
`question` object, not in the `quote` fields, and their top-level `label` is
empty, so reading them like a quote yields nothing at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forrt_research_mcp import constellation as C

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(params=["pico", "pcc"])
def anchor(request):
    raw = json.loads((FIXTURES / f"question-{request.param}.json").read_text())
    return request.param.upper(), raw


@pytest.fixture
def pico():
    return json.loads((FIXTURES / "question-pico.json").read_text())


@pytest.fixture
def pcc():
    return json.loads((FIXTURES / "question-pcc.json").read_text())


class TestTheFixturesAreWhatWeThink:
    def test_each_is_a_standalone_question_nanopub(self, anchor):
        _, raw = anchor
        assert raw["nodeCount"] == 1 and raw["edgeCount"] == 0
        assert raw["nodes"][0]["stepKind"] == "question"
        assert not raw["chains"]

    def test_the_node_label_is_empty_which_is_why_a_quote_reader_returns_nothing(
            self, anchor):
        _, raw = anchor
        assert raw["nodes"][0]["label"] == ""
        assert raw["nodes"][0].get("quote") is None


class TestExtraction:
    def test_the_anchor_is_surfaced_with_its_content(self, anchor):
        kind, raw = anchor
        upstream = C.summary("x", raw=raw)["upstream"]
        assert len(upstream) == 1
        entry = upstream[0]
        assert entry["step"] == "Question"
        assert entry["framework"] == kind
        assert entry["text_source"] == "structured"
        assert len(entry["text"]) > 80, "the question text must come through"
        assert entry["label"], "the short label must come through"

    def test_pico_carries_its_four_components_in_order(self, pico):
        entry = C.summary("x", raw=pico)["upstream"][0]
        assert [c["key"] for c in entry["components"]] == [
            "population", "intervention", "comparator", "outcome"]
        assert all(c["text"] for c in entry["components"])

    def test_pcc_carries_its_three_components(self, pcc):
        entry = C.summary("x", raw=pcc)["upstream"][0]
        assert [c["key"] for c in entry["components"]] == [
            "population", "concept", "context"]
        assert all(c["text"] for c in entry["components"])

    def test_the_two_frameworks_are_not_interchangeable(self, pico, pcc):
        """PICO has a comparator and an outcome; PCC has a concept and a
        context. Treating them as one form loses the distinction."""
        pico_keys = {c["key"] for c in C.summary("x", raw=pico)["upstream"][0]["components"]}
        pcc_keys = {c["key"] for c in C.summary("x", raw=pcc)["upstream"][0]["components"]}
        assert "comparator" in pico_keys and "comparator" not in pcc_keys
        assert "concept" in pcc_keys and "concept" not in pico_keys


class TestNoCitedPaper:
    """A question-rooted chain need not start from an existing work, so there is
    nothing to cite and nothing to filter anchors by."""

    def test_no_cited_paper_is_reported_as_unknown_not_invented(self, anchor):
        _, raw = anchor
        paper = C.cited_paper(raw)
        assert paper["doi"] == "" and paper["source"] == "unknown"
        assert paper["disagreesWithReported"] is False

    def test_a_question_anchor_survives_the_paper_filter(self, anchor):
        """Quotes are filtered by the cited paper; questions cite nothing, so
        filtering them the same way would drop every one."""
        _, raw = anchor
        assert len(C._upstream_nodes(raw, paper_doi="")) == 1
        assert len(C._upstream_nodes(raw, paper_doi="https://doi.org/10.1/other")) == 1

    def test_prior_work_reports_no_chains_without_inventing_any(self, anchor):
        _, raw = anchor
        prior = C.prior_work("x", raw=raw)
        assert prior["replicationCount"] == 0 and prior["verdicts"] == []
        assert len(prior["upstreamAnchors"]) == 1


class TestEntryDepth:
    """The walk crosses Claim -> AIDA (the platform bridges the shared
    purl.org/aida/ statement IRI) but NOT AIDA -> Claim, which is not a nanopub
    reference and so has no npa:refersToNanopub edge to follow upstream.

    Consequence: entering at an anchor or an AIDA finds no chain even when a
    complete six-step chain is published on it. Both fixtures below are the SAME
    published chain, entered at different depths."""

    @pytest.fixture(params=["pico", "pcc"])
    def at_aida(self, request):
        return json.loads((FIXTURES / f"question-{request.param}-aida-entry.json").read_text())

    @pytest.fixture(params=["pico", "pcc"])
    def full(self, request):
        return json.loads((FIXTURES / f"question-{request.param}-full-chain.json").read_text())

    def test_entering_at_the_aida_reaches_only_the_anchor(self, at_aida):
        assert at_aida["nodeCount"] == 2
        assert not at_aida["chains"]

    def test_a_shallow_entry_says_so_instead_of_implying_no_chain_exists(self, at_aida):
        view = C.summary("x", raw=at_aida)
        assert "entryTooShallow" in view
        assert "Re-enter at the Claim or deeper" in view["entryTooShallow"]

    def test_entering_at_the_claim_reaches_the_whole_chain(self, full):
        view = C.summary("x", raw=full)
        assert len(view["chains"]) == 1
        assert view["chains"][0]["stepsPresent"] == [
            "Question", "AIDA", "Claim", "Study", "Outcome", "CiTO"]
        assert "entryTooShallow" not in view

    def test_a_complete_chain_carries_its_question_anchor_as_a_step(self, full):
        """The Question is enumerated as a chain step here, not merely as an
        upstream anchor — a question-rooted chain is complete in itself."""
        question = next(s for s in C.summary("x", raw=full)["chains"][0]["steps"]
                        if s["step"] == "Question")
        assert question["framework"] in ("PICO", "PCC")
        assert question["components"] and question["text"]

    def test_these_are_from_scratch_chains_crediting_their_tools(self, full):
        """Both credit the software they used rather than confirming a prior
        finding — `credits` asserts no verdict, so the verdict cross-check must
        not fire."""
        chain = C.summary("x", raw=full)["chains"][0]
        assert chain["verdict"] == "Validated"
        assert chain["citoRelations"] == ["credits"]
