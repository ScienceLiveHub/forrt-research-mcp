"""DOI and Wikidata resolution.

Hermetic: HTTP is stubbed with real recorded responses, trimmed. The behaviour
under test is not "can we call an API" but "does a fabricated identifier get
through" — so most of these are rejection tests.
"""
from __future__ import annotations

import json

import pytest

from forrt_research_mcp import grounding as G
from forrt_research_mcp.grounding import (
    GroundingError,
    normalise_doi,
    resolve_doi,
    wikidata_lookup,
)

# Trimmed from the live CSL JSON for 10.1038/s41467-018-03732-9.
OLIVER_CSL = {
    "DOI": "10.1038/s41467-018-03732-9",
    "title": "Longer and more frequent marine heatwaves over the past century",
    "container-title": "Nature Communications",
    "type": "journal-article",
    "publisher": "Springer Science and Business Media LLC",
    "issued": {"date-parts": [[2018, 4, 10]]},
    "author": [
        {"given": "Eric C. J.", "family": "Oliver"},
        {"given": "Markus G.", "family": "Donat"},
    ],
}


def stub_http(monkeypatch, status: int, body: str = ""):
    monkeypatch.setattr(G, "_get", lambda url, accept, timeout=30: (status, body))


class TestDoiNormalisation:
    @pytest.mark.parametrize("given", [
        "10.1038/s41467-018-03732-9",
        "https://doi.org/10.1038/s41467-018-03732-9",
        "http://dx.doi.org/10.1038/s41467-018-03732-9",
        "doi:10.1038/s41467-018-03732-9",
        "  10.1038/s41467-018-03732-9  ",
    ])
    def test_every_common_form_reduces_to_the_bare_doi(self, given):
        assert normalise_doi(given) == "10.1038/s41467-018-03732-9"


class TestResolveDoi:
    def test_a_registered_doi_returns_its_metadata(self, monkeypatch):
        stub_http(monkeypatch, 200, json.dumps(OLIVER_CSL))
        r = resolve_doi("https://doi.org/10.1038/s41467-018-03732-9")
        assert r["resolves"] is True
        assert r["title"].startswith("Longer and more frequent")
        assert r["year"] == "2018"
        assert r["authors"] == ["Eric C. J. Oliver", "Markus G. Donat"]
        assert r["container"] == "Nature Communications"

    def test_a_fabricated_but_well_formed_doi_is_rejected(self, monkeypatch):
        """The failure this exists to catch: a DOI that looks entirely real."""
        stub_http(monkeypatch, 404)
        r = resolve_doi("10.1038/s41467-018-99999999")
        assert r["resolves"] is False
        assert r["status"] == 404
        assert "Do NOT publish" in r["note"]

    @pytest.mark.parametrize("bad", ["not-a-doi", "10.x/foo", "", "   ", "12.345/x"])
    def test_malformed_input_is_refused_before_any_request(self, bad):
        with pytest.raises(GroundingError):
            resolve_doi(bad)

    def test_a_server_error_is_not_reported_as_a_bad_doi(self, monkeypatch):
        """A 500 means 'ask again', not 'this DOI is fake' — conflating them
        would have a caller delete a perfectly good citation."""
        stub_http(monkeypatch, 503)
        r = resolve_doi("10.1038/s41467-018-03732-9")
        assert r["resolves"] is False
        assert "transient" in r["note"]

    def test_resolving_but_unparsable_metadata_still_counts_as_resolving(self, monkeypatch):
        stub_http(monkeypatch, 200, "<html>not json</html>")
        r = resolve_doi("10.1038/s41467-018-03732-9")
        assert r["resolves"] is True


class TestWikidataLookup:
    SEARCH = {"search": [
        {"id": "Q25407", "label": "Bombus", "description": "genus of insects"},
        {"id": "Q16244533", "label": "Bombus", "description": "album by Bombus"},
    ]}

    def stub_api(self, monkeypatch, types_by_qid):
        """Stub the three call shapes: search, claims, labels."""
        def fake(params):
            if params["action"] == "wbsearchentities":
                return self.SEARCH
            ids = params["ids"].split("|")
            if params["props"] == "claims":
                return {"entities": {
                    q: {"claims": {"P31": [
                        {"mainsnak": {"datavalue": {"value": {"id": t}}}}
                        for t in types_by_qid.get(q, [])
                    ]}} for q in ids}}
            return {"entities": {q: {"labels": {"en": {"value": f"label-{q}"}}}}
                    for q in ids}
        monkeypatch.setattr(G, "_wikidata_api", fake)

    def test_candidates_are_returned_with_their_real_types(self, monkeypatch):
        self.stub_api(monkeypatch, {"Q25407": ["Q16521"], "Q16244533": ["Q482994"]})
        r = wikidata_lookup("Bombus")
        assert r["count"] == 2
        assert r["candidates"][0]["qid"] == "Q25407"
        assert r["candidates"][0]["types"][0]["qid"] == "Q16521"

    def test_type_checking_separates_the_genus_from_the_album(self, monkeypatch):
        """The disambiguation failure this tool exists to prevent: two items
        share the label 'Bombus' and only one is a taxon."""
        self.stub_api(monkeypatch, {"Q25407": ["Q16521"], "Q16244533": ["Q482994"]})
        r = wikidata_lookup("Bombus", expected_type="Q16521")
        matches = {c["qid"]: c["typeMatches"] for c in r["candidates"]}
        assert matches == {"Q25407": True, "Q16244533": False}
        assert r["matchingCount"] == 1

    def test_candidates_are_annotated_not_filtered(self, monkeypatch):
        """A near miss is informative; silently dropping it hides the ambiguity."""
        self.stub_api(monkeypatch, {"Q25407": ["Q16521"], "Q16244533": ["Q482994"]})
        assert wikidata_lookup("Bombus", expected_type="Q16521")["count"] == 2

    def test_no_match_says_leave_it_empty_rather_than_offering_nothing(self, monkeypatch):
        monkeypatch.setattr(G, "_wikidata_api", lambda params: {"search": []})
        r = wikidata_lookup("zzqqxx not a real concept")
        assert r["count"] == 0 and r["candidates"] == []
        assert "Do NOT invent a QID" in r["note"]

    @pytest.mark.parametrize("bad", ["P31", "16521", "Q", "taxon", "L123"])
    def test_expected_type_must_be_an_item_qid(self, bad):
        with pytest.raises(GroundingError, match="QID"):
            wikidata_lookup("Bombus", expected_type=bad)

    def test_an_empty_query_is_refused(self):
        with pytest.raises(GroundingError, match="empty query"):
            wikidata_lookup("   ")

    def test_the_note_never_asserts_a_choice(self, monkeypatch):
        """The tool must not appear to endorse a candidate — choosing the right
        sense is the caller's judgement."""
        self.stub_api(monkeypatch, {"Q25407": ["Q16521"], "Q16244533": ["Q482994"]})
        note = wikidata_lookup("Bombus", expected_type="Q16521")["note"]
        assert "candidates only" in note
        assert "none is asserted" in note
