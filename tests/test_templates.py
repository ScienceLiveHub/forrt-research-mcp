"""Template schemas and controlled vocabularies.

Hermetic: the bundled snapshot covers the offline paths, and the live paths are
exercised with stubbed fetches so the suite never depends on the nanopub
network. The recorded fragments are real TriG, trimmed.
"""
from __future__ import annotations

import pytest

from forrt_research_mcp import templates as T
from forrt_research_mcp.api import ApiError


@pytest.fixture(autouse=True)
def _clear_caches():
    """`_live_spec` is lru_cached; stubs must not leak between tests."""
    T._live_spec.cache_clear()
    yield
    T._live_spec.cache_clear()


class TestStepResolution:
    def test_every_registry_step_is_listed(self):
        assert "05_outcome" in T.steps()
        assert len(T.steps()) == 10

    @pytest.mark.parametrize("alias", ["05_outcome", "05", "outcome"])
    def test_a_step_can_be_named_three_ways(self, alias):
        assert T._resolve_step(alias) == "05_outcome"

    def test_an_unknown_step_lists_the_real_ones_rather_than_guessing(self):
        with pytest.raises(ApiError, match="Known steps"):
            T._resolve_step("outcomes")


class TestOfflineSnapshot:
    def test_fields_come_back_without_a_network(self):
        spec = T.template_fields("05_outcome", live=False)
        assert spec["source"] == "bundled-snapshot"
        assert [f["id"] for f in spec["fields"]][:3] == ["outcome", "label", "study"]

    def test_the_snapshot_carries_the_constraints_that_matter(self):
        """The Quote template's character cap lives in the field spec, which is
        the whole reason to read the template instead of a hand-written doc."""
        quote = T.template_fields("01_quote", live=False)
        assert any(f.get("regex") for f in quote["fields"])


class TestLiveAndFallback:
    def test_a_network_failure_falls_back_and_says_so(self, monkeypatch):
        monkeypatch.setattr(T, "fetch_trig",
                            lambda uri, **kw: (_ for _ in ()).throw(ApiError("down")))
        spec = T.template_fields("05_outcome", live=True)
        assert spec["source"] == "bundled-snapshot"
        assert "may be out of date" in spec["warning"]
        # Still usable — the caller gets real field names, just possibly stale.
        assert [f["id"] for f in spec["fields"]][0] == "outcome"

    def test_a_live_spec_matching_the_snapshot_reports_no_drift(self, monkeypatch):
        monkeypatch.setattr(T, "_live_spec",
                            lambda uri: dict(T.snapshot()["05_outcome"]))
        spec = T.template_fields("05_outcome", live=True)
        assert spec["source"] == "live"
        assert spec["driftedFromSnapshot"] is False
        assert "note" not in spec

    def test_drift_is_reported_loudly_rather_than_silently_preferred(self, monkeypatch):
        """A superseded template is exactly what silently rots hand-written docs."""
        changed = dict(T.snapshot()["05_outcome"])
        changed["fields"] = changed["fields"] + [
            {"id": "newField", "label": "added upstream", "kind": "literal",
             "required": True}
        ]
        monkeypatch.setattr(T, "_live_spec", lambda uri: changed)
        spec = T.template_fields("05_outcome", live=True)
        assert spec["driftedFromSnapshot"] is True
        assert "re-vendored" in spec["note"]
        assert any(f["id"] == "newField" for f in spec["fields"])


class TestVocabularies:
    def test_every_declared_vocabulary_resolves_offline_or_says_why_not(self):
        for name in T.VOCABULARIES:
            result = T.vocabulary(name, live=False)
            assert result["values"] or result["source"] == "unavailable-offline"

    @pytest.mark.parametrize("name,expected", [
        ("validation_status", 5),
        ("confidence_level", 5),
        ("claim_type", 7),
        ("study_type", 3),
        ("question_type", 5),
    ])
    def test_inline_enumerations_come_from_the_template(self, name, expected):
        result = T.vocabulary(name, live=False)
        assert result["count"] == expected
        assert all(v["uri"] for v in result["values"])

    def test_the_outcome_verdict_offers_all_five_statuses(self):
        """The template repo's own notes record a 3-vs-5 drift here, so pin it."""
        labels = {v["label"] for v in T.vocabulary("validation_status", live=False)["values"]}
        assert labels == {"validated", "partially supported", "contradicted",
                          "inconclusive", "not tested"}

    def test_study_type_carries_the_reproduction_replication_distinction(self):
        labels = " ".join(v["label"].lower()
                          for v in T.vocabulary("study_type", live=False)["values"])
        assert "reproduction study" in labels and "replication study" in labels

    def test_an_unknown_vocabulary_lists_the_real_ones(self):
        with pytest.raises(ApiError, match="Known:"):
            T.vocabulary("claim_types", live=False)


class TestValueListVocabulary:
    """CiTO relations live in a separate value-list nanopub, not inline."""

    TRIG = """
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<https://w3id.org/np/RAlist> {
  <http://purl.org/spar/cito/confirms> rdfs:label "confirms - ..." .
  <http://purl.org/spar/cito/disputes> rdfs:label "disputes - ..." .
  <http://purl.org/spar/cito/extends> rdfs:label "extends - ..." .
}
"""

    def test_offline_it_refuses_rather_than_returning_an_empty_list(self):
        """An empty vocabulary would read as 'no valid values', which is worse
        than saying it cannot be resolved."""
        result = T.vocabulary("cito_relation", live=False)
        assert result["source"] == "unavailable-offline"
        assert result["values"] == []
        assert "live=True" in result["warning"]

    def test_live_it_resolves_the_separate_value_list(self, monkeypatch):
        monkeypatch.setattr(T, "_live_spec",
                            lambda uri: dict(T.snapshot()["06_citation"]))
        monkeypatch.setattr(T, "fetch_trig", lambda uri, **kw: self.TRIG)
        result = T.vocabulary("cito_relation", live=True)
        assert result["source"] == "live-value-list"
        assert {v["uri"].rsplit("/", 1)[-1] for v in result["values"]} == {
            "confirms", "disputes", "extends"}

    def test_a_value_list_that_cannot_be_fetched_is_reported_not_faked(self, monkeypatch):
        monkeypatch.setattr(T, "_live_spec",
                            lambda uri: dict(T.snapshot()["06_citation"]))
        monkeypatch.setattr(T, "fetch_trig",
                            lambda uri, **kw: (_ for _ in ()).throw(ApiError("404")))
        result = T.vocabulary("cito_relation", live=True)
        assert result["source"] == "unavailable"
        assert result["values"] == []


def test_vocabulary_mappings_all_point_at_real_fields():
    """Guards the mapping itself: if a template renames a field, this fails
    here rather than silently returning nothing at drafting time."""
    for name, (step, field_id) in T.VOCABULARIES.items():
        spec = T.template_fields(step, live=False)
        ids = [f["id"] for f in spec["fields"]]
        assert field_id in ids, f"{name}: {field_id!r} not in {step} ({ids})"
