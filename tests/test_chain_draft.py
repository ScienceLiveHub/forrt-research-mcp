"""Validation of `chain-draft.json` — the artifact handed to the chain wizard.

Written as mutation tests: start from a draft shaped like the two real ones,
break exactly one thing, and assert it is caught. A checker that passes
everything is worthless, and a checker that fails correct drafts is worse than
none — both halves are asserted here.
"""
from __future__ import annotations

import copy
import json

import pytest

from forrt_research_mcp import chain_draft as CD
from forrt_research_mcp.api import ApiError

CLAIM_TPL = "https://w3id.org/np/RAZWyM8D16ya3S1zhCvrG1f0iSpd9-8onVWp0FTvvX7LQ"
SUPERSEDED_CLAIM = "https://w3id.org/np/RAu5uTahAxc0OLBB3vaGwK3OQDDZV7QuWtDlBk0Ea3bco"


def _tpl(step: str) -> str:
    return CD.registry()["steps"][step]["current"]


GOOD = {
    "schema_version": "1.0",
    "kind": "forrt-chain-draft",
    "chain_shape": "paper-rooted",
    "source": {"repository": "https://github.com/org/repo"},
    "steps": [
        {"step": "01_quote", "template_key": "ANNOTATE_QUOTATION",
         "template_uri": _tpl("01_quote"),
         "prefill": {"paper": "10.3390/rs13051043",
                     "quotation": "A sentence quoted verbatim from the paper here.",
                     "comment": "Why this sentence carries the claim under test."}},
        {"step": "02_aida", "template_key": "AIDA_SENTENCE",
         "template_uri": _tpl("02_aida"),
         "prefill": {"aida": "Something measurable increased over the record."}},
        {"step": "03_claim", "template_key": "FORRT_CLAIM",
         "template_uri": _tpl("03_claim"),
         "prefill": {"claim": "a-claim-slug", "label": "A claim label",
                     "forrtType": "descriptive pattern"}},
        {"step": "04_study", "template_key": "FORRT_REPLICATION",
         "template_uri": _tpl("04_study"),
         "prefill": {"study": "a-study-slug", "label": "A study label",
                     "type": "Replication Study",
                     "scope": "What part of the claim is tested.",
                     "methodology": "How it was tested.",
                     "disciplineSelection": {"uri": "http://x/Q1", "label": "d"}}},
        {"step": "05_outcome", "template_key": "FORRT_REPLICATION_OUTCOME",
         "template_uri": _tpl("05_outcome"),
         "prefill": {"outcome": "an-outcome-slug", "label": "An outcome label",
                     "repo": "https://doi.org/10.5281/zenodo.21561994",
                     "date": "2026-08-15", "validationStatus": "validated",
                     "conclusion": "The claim is validated.",
                     "evidence": "The numbers that support it.",
                     "confidenceLevel": "high"}},
        {"step": "06_citation", "template_key": "CITATION_CITO",
         "template_uri": _tpl("06_citation"),
         "prefill": {"st02": [{"cites": "http://purl.org/spar/cito/confirms",
                               "cited": "https://doi.org/10.3390/rs13051043"}]}},
    ],
    "carry_forward": [
        {"from": "01_quote", "into": "02_aida", "field": "project"},
        {"from": "02_aida", "into": "03_claim", "field": "aida"},
        {"from": "03_claim", "into": "04_study", "field": "claim"},
        {"from": "04_study", "into": "05_outcome", "field": "study"},
        {"from": "05_outcome", "into": "06_citation", "field": "work"},
    ],
}


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network: the bundled snapshot supplies the templates, DOIs resolve."""
    real = CD.template_fields
    monkeypatch.setattr(CD, "template_fields",
                        lambda step, live=True: real(step, live=False))
    real_vocab = CD.vocabulary
    monkeypatch.setattr(CD, "vocabulary",
                        lambda name, live=True: real_vocab(name, live=False))
    monkeypatch.setattr(CD, "resolve_doi", lambda doi: {
        "doi": doi, "resolves": True, "status": 200, "title": "A resolvable thing"})


def check(tmp_path, mutate=None) -> dict:
    draft = copy.deepcopy(GOOD)
    if mutate:
        mutate(draft)
    path = tmp_path / "chain-draft.json"
    path.write_text(json.dumps(draft))
    return CD.validate_chain_draft(str(path))


def step_of(draft: dict, name: str) -> dict:
    return next(s for s in draft["steps"] if s["step"] == name)


def errors(result: dict, check_name: str) -> list[dict]:
    return [f for f in result["findings"]
            if f["severity"] == "error" and f["check"] == check_name]


class TestAcceptsACorrectDraft:
    def test_a_well_formed_draft_is_ready(self, tmp_path):
        result = check(tmp_path)
        assert result["readyForWizard"] is True, result["findings"]
        assert result["counts"]["error"] == 0

    def test_carry_forward_fields_are_not_reported_missing(self, tmp_path):
        """02_aida has no `project`, 03_claim no `aida`, 04_study no `claim` —
        the wizard fills each from the step published before it."""
        result = check(tmp_path)
        assert errors(result, "required") == []

    def test_a_directory_finds_the_draft_inside_it(self, tmp_path):
        check(tmp_path)
        assert CD.validate_chain_draft(str(tmp_path))["readyForWizard"] is True


class TestTemplateUri:
    """A stale template_uri is invisible in the JSON and fatal at the wizard."""

    def test_a_superseded_template_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "03_claim").__setitem__(
            "template_uri", SUPERSEDED_CLAIM))
        found = errors(result, "template-uri")
        assert found and "SUPERSEDED" in found[0]["message"]
        assert "build-chain-draft" in found[0]["message"]

    def test_an_unrecognised_template_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "03_claim").__setitem__(
            "template_uri", "https://w3id.org/np/RAnonsense"))
        assert errors(result, "template-uri")

    def test_the_current_template_passes(self, tmp_path):
        assert errors(check(tmp_path), "template-uri") == []


class TestPrefillKeys:
    def test_an_invented_field_name_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "05_outcome")["prefill"]
                       .__setitem__("verdict", "validated"))
        found = errors(result, "field-exists")
        assert found and "wizard will drop it" in found[0]["message"]

    def test_platform_form_fields_are_accepted_not_flagged(self, tmp_path):
        """`st02` and `disciplineSelection` are form-field names, not template
        placeholders. A naive key check would reject both correct drafts."""
        result = check(tmp_path)
        assert errors(result, "field-exists") == []

    def test_a_required_field_that_is_dropped_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "05_outcome")["prefill"]
                       .pop("conclusion"))
        assert errors(result, "required")


class TestComplexFieldShapes:
    def test_an_empty_repeatable_group_is_caught(self, tmp_path):
        """`st02` needs >=1 entry or the form will not submit."""
        result = check(tmp_path, lambda d: step_of(d, "06_citation")["prefill"]
                       .__setitem__("st02", []))
        found = errors(result, "shape")
        assert found and "at least 1 entry" in found[0]["message"]

    def test_an_incomplete_citation_entry_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "06_citation")["prefill"]
                       .__setitem__("st02", [{"cites": "http://purl.org/spar/cito/confirms"}]))
        found = errors(result, "shape")
        assert found and "cited" in found[0]["message"]

    def test_discipline_selection_must_be_an_object_not_an_array(self, tmp_path):
        """The one asymmetry in the contract: every other selection field is a
        list, this one is a single object."""
        result = check(tmp_path, lambda d: step_of(d, "04_study")["prefill"]
                       .__setitem__("disciplineSelection", [{"uri": "x", "label": "y"}]))
        found = errors(result, "shape")
        assert found and "NOT an array" in found[0]["message"]

    def test_a_group_given_a_string_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "06_citation")["prefill"]
                       .__setitem__("st02", "confirms"))
        assert errors(result, "shape")


class TestValues:
    def test_a_value_over_the_template_cap_is_caught(self, tmp_path):
        """The Quote's 500-character cap lives in the template regex."""
        result = check(tmp_path, lambda d: step_of(d, "01_quote")["prefill"]
                       .__setitem__("quotation", "x" * 501))
        found = errors(result, "constraint")
        assert found and "501 characters" in found[0]["message"]

    def test_a_value_at_the_cap_passes(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "01_quote")["prefill"]
                       .__setitem__("quotation", "x" * 500))
        assert errors(result, "constraint") == []

    def test_a_vocabulary_value_the_form_rejects_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "05_outcome")["prefill"]
                       .__setitem__("validationStatus", "mostly validated"))
        assert errors(result, "vocabulary")

    def test_a_malformed_date_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: step_of(d, "05_outcome")["prefill"]
                       .__setitem__("date", "15/08/2026"))
        found = errors(result, "value")
        assert found and "YYYY-MM-DD" in found[0]["message"]

    def test_an_unresolved_release_token_is_caught(self, tmp_path):
        """`{{ZENODO_VERSION_DOI}}` is fine in a markdown draft — the release
        workflow substitutes it. By the time it reaches chain-draft.json it must
        be resolved, or it gets signed literally."""
        result = check(tmp_path, lambda d: step_of(d, "05_outcome")["prefill"]
                       .__setitem__("repo", "https://doi.org/{{ZENODO_VERSION_DOI}}"))
        assert errors(result, "placeholder")

    def test_an_unregistered_doi_is_caught(self, tmp_path, monkeypatch):
        monkeypatch.setattr(CD, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False, "status": 404})
        assert errors(check(tmp_path), "doi")

    def test_a_resolver_outage_warns_rather_than_blocking(self, tmp_path, monkeypatch):
        monkeypatch.setattr(CD, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False, "status": 503})
        result = check(tmp_path)
        assert result["readyForWizard"] is True
        assert any(f["check"] == "doi" and f["severity"] == "warning"
                   for f in result["findings"])


class TestCarryForward:
    def test_a_backwards_edge_is_caught(self, tmp_path):
        """The wizard publishes in order and cannot carry a URI from a step it
        has not reached."""
        result = check(tmp_path, lambda d: d.__setitem__("carry_forward", [
            {"from": "05_outcome", "into": "02_aida", "field": "project"}]))
        assert errors(result, "carry-forward")

    def test_an_edge_naming_an_absent_step_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: d["carry_forward"].append(
            {"from": "08_synthesis", "into": "06_citation", "field": "work"}))
        assert errors(result, "carry-forward")

    def test_prefilling_a_carried_field_is_only_a_warning(self, tmp_path):
        """Harmless — the wizard's value wins — but dead weight worth flagging."""
        result = check(tmp_path, lambda d: step_of(d, "03_claim")["prefill"]
                       .__setitem__("aida", "https://w3id.org/np/RAsomething"))
        assert result["readyForWizard"] is True
        assert any(f["check"] == "carry-forward" and f["severity"] == "warning"
                   for f in result["findings"])


class TestEnvelope:
    def test_the_wrong_kind_is_caught(self, tmp_path):
        result = check(tmp_path, lambda d: d.__setitem__("kind", "something-else"))
        assert errors(result, "envelope")

    def test_an_unknown_schema_version_warns(self, tmp_path):
        result = check(tmp_path, lambda d: d.__setitem__("schema_version", "9.9"))
        assert any(f["check"] == "envelope" and f["severity"] == "warning"
                   for f in result["findings"])

    def test_no_steps_is_an_error(self, tmp_path):
        result = check(tmp_path, lambda d: d.__setitem__("steps", []))
        assert result["readyForWizard"] is False

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(ApiError, match="no chain draft"):
            CD.validate_chain_draft(str(tmp_path / "nope.json"))

    def test_invalid_json_says_so(self, tmp_path):
        p = tmp_path / "chain-draft.json"
        p.write_text("{not json")
        with pytest.raises(ApiError, match="not valid JSON"):
            CD.validate_chain_draft(str(p))
