"""A second real constellation, with a different chain shape.

Everything in `test_constellation.py` was fitted to one sample. This file pins
the behaviours that only a differently-shaped chain can exercise, using the
Sado-estuary constellation (fetched 2026-09-02 from api-dev): TWO limbs with
opposing verdicts, a Research Synthesis at the apex instead of a CiTO, and a
walk that terminates before reaching the Quote and Claim steps.

`edges` is emptied in this fixture — it is half the payload and no code path
reads it — while `edgeCount` is preserved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forrt_research_mcp import constellation as C

FIXTURE = Path(__file__).parent / "fixtures" / "constellation-sado.json"

# The paper this chain actually qualifies/confirms (Sentinel-2, Westerschelde).
SENTINEL2 = "https://doi.org/10.3390/rs13051043"
# The unrelated paper top-level `paperDoi` reports — the SAME wrong answer it
# gives on the marine-heatwave chain, from a different entry point.
LIFEWATCH = "https://doi.org/10.3897/rio.10.e119943"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(FIXTURE.read_text())


def test_fixture_still_has_the_shape_these_tests_assume(raw):
    assert len(raw["chains"]) == 2
    assert raw["researchSynthesis"] is not None
    assert raw["apexCito"] is None


# What api-dev actually returned for this entry URI, measured before `edges` was
# emptied to keep the fixture small. The projection claim is about the real
# payload, so the ratio is asserted against this rather than the trimmed copy.
UNTRIMMED_BYTES = 396_529


def test_projection_holds_on_a_second_chain(raw):
    """4.9 % here and 4.7 % on the marine-heatwave chain — not a one-off."""
    view = C.summary("x", raw=raw)
    assert len(json.dumps(view)) < UNTRIMMED_BYTES * 0.10


class TestMultipleLimbs:
    """Two independent replications of one paper, reaching different verdicts."""

    def test_both_limbs_are_kept_separate(self, raw):
        chains = C.summary("x", raw=raw)["chains"]
        assert len(chains) == 2
        assert {c["verdict"] for c in chains} == {"Validated", "PartiallySupported"}

    def test_each_limb_keeps_its_own_cito_relation(self, raw):
        relations = {c["verdict"]: c["citoRelations"] for c in C.summary("x", raw=raw)["chains"]}
        assert relations["Validated"] == ["confirms"]
        assert relations["PartiallySupported"] == ["qualifies"]

    def test_prior_work_reports_every_limb_and_its_verdicts(self, raw):
        prior = C.prior_work("x", raw=raw)
        assert prior["replicationCount"] == 2
        assert prior["verdicts"] == ["PartiallySupported", "Validated"]


class TestResearchSynthesisApex:
    def test_the_synthesis_is_surfaced(self, raw):
        synthesis = C.summary("x", raw=raw)["researchSynthesis"]
        assert synthesis["uri"].endswith("Buuif2s")
        assert "Westerschelde" in synthesis["label"]

    def test_an_absent_apex_cito_is_none_not_an_empty_dict(self, raw):
        """This constellation's apex is the Synthesis; there is no apex CiTO."""
        assert C.summary("x", raw=raw)["apexCito"] is None


class TestReplicatedPaperOnASecondChain:
    def test_the_cito_still_wins_over_reported_paper_doi(self, raw):
        paper = C.cited_paper(raw)
        assert paper["doi"] == SENTINEL2
        assert paper["source"] == "cito-citedTargets"

    def test_the_upstream_bug_reproduces_from_a_different_entry_point(self, raw):
        """`paperDoi` names the same unrelated paper here as on the
        marine-heatwave chain, so the fault is systemic rather than one bad
        record. If this test ever fails, the upstream bug was fixed."""
        paper = C.cited_paper(raw)
        assert paper["reportedPaperDoi"] == LIFEWATCH
        assert paper["disagreesWithReported"] is True


class TestIncompleteWalks:
    """The walk can stop short of the upstream anchors. Missing is not
    unpublished — both limbs' Claims and the chain's Quote are published (they
    are listed in the repo's PUBLISHED.md) yet absent from this payload."""

    PUBLISHED_BUT_ABSENT = [
        "RAXx0A9g5UJ5AM686y-9FOn5bwEdmb45xqhgVoNkCo3Pc",  # Quote
        "RAuzyXFNXL_jqMA3b5isvsSW3W-LNjqGrMaeKdCW_VLWc",  # Claim, limb 1
        "RA9BeRdRHcUCwth2RWceokYNHF403kh_f9pzVW0S01IVM",  # Claim, limb 2
    ]

    @pytest.mark.parametrize("suffix", PUBLISHED_BUT_ABSENT)
    def test_published_steps_can_be_missing_from_the_constellation(self, raw, suffix):
        assert not any(suffix in n["uri"] for n in raw["nodes"])

    def test_the_gap_is_reported_rather_than_silently_dropped(self, raw):
        for chain in C.summary("x", raw=raw)["chains"]:
            assert chain["stepsPresent"] == ["Study", "Outcome", "CiTO"]
            # Callers must know to fetch these directly instead of concluding
            # the chain is incomplete.
            assert {"Claim", "AIDA"} <= set(chain["stepsNotEnumerated"])

    def test_claim_type_is_empty_when_the_claim_was_not_reached(self, raw):
        assert all(e["claimType"] == "" for e in C.prior_work("x", raw=raw)["priorWork"])


def test_foreign_quotes_are_excluded_here_too(raw):
    """The same 4 unrelated quote nodes appear in both constellations — the walk
    routes through a shared hub — and must not be attributed to this paper."""
    assert sum(1 for n in raw["nodes"] if n["stepKind"] == "quote") == 4
    assert C.summary("x", raw=raw)["upstream"] == []
