"""CiTO nanopublications -> Wikidata QuickStatements.

Hermetic: the nanopublications and the Zenodo record are real recorded responses
(the Soroye et al. 2020 replication, CiTO `confirms`); Wikidata lookups are
stubbed per test, because what is under test is the decision each lookup result
leads to: create or reuse, add or skip, now or on the next run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forrt_research_mcp import server
from forrt_research_mcp import wikidata_cito as W

FIX = Path(__file__).parent / "fixtures" / "wikidata-cito"
CITO_NP = "https://w3id.org/sciencelive/np/RApCQTLMP8h0jDYF9ggWU6lMTW7a_KG5_Jygbsx0aTpIo"
OUTCOME_NP = "https://w3id.org/sciencelive/np/RAIylrhtnfTH_vtp1nTDVAiGsv2u_Ea4Uvh35DrGySuWs"
SOROYE = "Q89582023"
CONFIRMS = "Q96480427"
# What Wikidata answers for the relations under test (each intent item's P2888).
INTENTS = {
    "http://purl.org/spar/cito/confirms": CONFIRMS,
    "http://purl.org/spar/cito/qualifies": "Q117121930",
    "http://purl.org/spar/cito/disputes": "Q117121923",
}


def zenodo_record() -> dict:
    data = json.loads((FIX / "zenodo-19762723.json").read_text())
    meta = data["metadata"]
    return {
        "concept_doi": f"10.5281/zenodo.{data['conceptrecid']}",
        "title": meta["title"], "date": meta["publication_date"],
        "type": meta["resource_type"]["type"],
        "creators": [{"name": W._display_name(c["name"]), "orcid": c.get("orcid", "")}
                     for c in meta["creators"]],
    }


@pytest.fixture
def offline(monkeypatch):
    """Real nanopubs + Zenodo record; Wikidata knows only the Soroye paper."""
    def fetch_trig(uri, timeout=30):
        return (FIX / f"{uri.rstrip('/').rsplit('/', 1)[-1]}.trig").read_text()

    items = {("P356", "10.1126/SCIENCE.AAX8591"): [SOROYE]}
    claims: dict[str, dict] = {}
    monkeypatch.setattr(W, "fetch_trig", fetch_trig)
    monkeypatch.setattr(W, "_intents", lambda: dict(INTENTS))
    monkeypatch.setattr(W, "_zenodo",
                        lambda doi: zenodo_record() if doi.lower().startswith("10.5281/zenodo.19") else None)
    monkeypatch.setattr(W, "_find", lambda prop, value: items.get((prop, value), []))
    monkeypatch.setattr(W, "_claims", lambda qid: claims.get(qid, {}))
    return {"items": items, "claims": claims}


def lines(result: dict) -> list[str]:
    return result["quickstatements"].splitlines()


class TestNewReplication:
    def test_creates_one_item_and_the_typed_citation(self, offline):
        result = W.wikidata_cito([CITO_NP], today="2026-10-19")
        qs = lines(result)
        assert qs.count("CREATE") == 1
        # One item per work: the concept DOI, not the version the outcome cites.
        assert 'LAST\tP356\t"10.5281/ZENODO.19756173"' in qs
        assert "LAST\tP31\tQ7397" in qs  # Zenodo software
        assert ('LAST\tP2860\tQ89582023\tP3712\tQ96480427'
                '\tS12545\t"https://w3id.org/np/RApCQTLMP8h0jDYF9ggWU6lMTW7a_KG5_Jygbsx0aTpIo"'
                '\tS813\t+2026-10-19T00:00:00Z/11') in qs

    def test_reference_and_outcome_link_use_the_form_scholia_counts(self, offline):
        qs = W.wikidata_cito([CITO_NP])["quickstatements"]
        assert "/sciencelive/np/" not in qs
        assert f'LAST\tP973\t"https://w3id.org/np/{OUTCOME_NP.rsplit("/", 1)[-1]}"' in qs

    def test_description_carries_the_doi_so_same_title_replications_do_not_collide(self, offline):
        qs = lines(W.wikidata_cito([CITO_NP]))
        assert 'LAST\tDen\t"replication study (2026), Zenodo 10.5281/zenodo.19756173"' in qs

    def test_author_without_an_item_becomes_a_name_string_in_display_order(self, offline):
        qs = lines(W.wikidata_cito([CITO_NP]))
        assert 'LAST\tP2093\t"Anne Fouilloux"\tP1545\t"1"' in qs

    def test_author_with_an_item_is_linked_by_orcid(self, offline):
        offline["items"][("P496", "0000-0002-1784-2920")] = ["Q999"]
        qs = lines(W.wikidata_cito([CITO_NP]))
        assert 'LAST\tP50\tQ999\tP1545\t"1"' in qs


class TestNoDuplicates:
    def test_two_versions_of_one_deposit_are_one_work(self, offline):
        # The same CiTO twice stands in for two outcomes citing two versions.
        result = W.wikidata_cito([CITO_NP, CITO_NP])
        assert lines(result).count("CREATE") == 1
        assert len(result["works"]) == 1

    def test_existing_item_is_reused_not_recreated(self, offline):
        offline["items"][("P356", "10.5281/ZENODO.19756173")] = ["Q123"]
        qs = lines(W.wikidata_cito([CITO_NP]))
        assert "CREATE" not in qs
        assert qs[0].startswith("Q123\tP2860\tQ89582023\tP3712\tQ96480427")

    def test_citation_already_in_wikidata_is_skipped(self, offline):
        offline["items"][("P356", "10.5281/ZENODO.19756173")] = ["Q123"]
        offline["claims"]["Q123"] = {"P2860": [{
            "mainsnak": {"datavalue": {"value": {"id": SOROYE}}},
            "qualifiers": {"P3712": [{"datavalue": {"value": {"id": CONFIRMS}}}]}}]}
        result = W.wikidata_cito([CITO_NP])
        assert result["quickstatements"] == ""
        assert result["works"][0]["citations"][0]["status"] == "already present"


class TestWhatCannotBeMirrored:
    def test_cited_work_without_an_item_is_reported_not_invented(self, offline):
        offline["items"].clear()
        result = W.wikidata_cito([CITO_NP])
        assert "P2860" not in result["quickstatements"]
        assert any("no Wikidata item yet" in s for s in result["skipped"])


class TestHelpers:
    @pytest.mark.parametrize("given, expected", [
        ("Fouilloux, Anne", "Anne Fouilloux"),
        ("Anne Fouilloux", "Anne Fouilloux"),
    ])
    def test_display_name(self, given, expected):
        assert W._display_name(given) == expected

    def test_intents_are_read_from_each_items_exact_match(self, monkeypatch):
        """Recorded shape of the two Wikidata API answers: the search for
        citation-intent items, then their claims."""
        def fake_json(url, params=None):
            # Two pages: the search pages its results, and stopping after the
            # first page once silently turned 'confirms' into a plain citation.
            if params.get("list") == "search" and params["sroffset"] == "0":
                return {"continue": {"sroffset": 1}, "query": {"search": [{"title": CONFIRMS}]}}
            if params.get("list") == "search":
                return {"query": {"search": [{"title": "Q117121919"}]}}
            return {"entities": {
                CONFIRMS: {"claims": {"P2888": [{"mainsnak": {"datavalue": {
                    "value": "http://purl.org/spar/cito/confirms"}}}]}},
                "Q117121919": {"claims": {"P2888": [{"mainsnak": {"datavalue": {
                    "value": "http://purl.org/spar/cito/credits"}}}]}},
            }}
        monkeypatch.setattr(W, "_json", fake_json)
        assert W._intents() == {W.CITO + "confirms": CONFIRMS, W.CITO + "credits": "Q117121919"}

    def test_unreachable_wikidata_fails_loudly_rather_than_guessing(self, monkeypatch):
        monkeypatch.setattr(W, "_json", lambda url, params=None: None)
        with pytest.raises(W.ApiError):
            W._intents()

    def test_published_ledger_yields_only_cito_nanopubs(self, offline, tmp_path):
        ledger = tmp_path / "PUBLISHED.md"
        ledger.write_text(f"| 05 | Outcome | {OUTCOME_NP} |\n| 06 | CiTO | {CITO_NP} |\n")
        assert W.cito_uris_from_published(str(tmp_path)) == [CITO_NP]


class TestServerTool:
    def test_tool_wraps_the_result(self, offline):
        r = server.cito_to_wikidata([CITO_NP])
        assert r["ok"] is True and r["statements"] > 0

    def test_tool_without_input_fails_as_data(self):
        r = server.cito_to_wikidata()
        assert r["ok"] is False
