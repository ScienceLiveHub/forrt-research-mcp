"""Draft validation.

Hermetic: drafts are written to tmp_path and the template/DOI lookups are
stubbed. The cases below are the ones real drafts actually produced — every
"false positive" fixed during development is pinned here so it cannot return.
"""
from __future__ import annotations

import pytest

from forrt_research_mcp import drafts as D
from forrt_research_mcp.api import ApiError

# Shaped like a real extracted spec, trimmed to what the checks read.
SPEC = {
    "step": "05_outcome",
    "templateUri": "https://w3id.org/np/RAoutcome",
    "source": "live",
    "fields": [
        {"id": "outcome", "label": "short URI suffix", "kind": "uri", "required": True},
        {"id": "study", "label": "choose study", "kind": "guided_choice",
         "required": True,
         "values_from_api": ["http://purl.org/nanopub/api/find_signed_things?x="]},
        {"id": "repo", "label": "repository URL", "kind": "external_uri", "required": True},
        {"id": "validationStatus", "label": "choose validation status",
         "kind": "restricted_choice", "required": True},
        {"id": "conclusion", "label": "conclusion", "kind": "long_literal",
         "required": True},
        {"id": "note", "label": "optional note", "kind": "literal", "required": False},
    ],
}

STATUSES = {
    "vocabulary": "validation_status", "step": "05_outcome",
    "field": "validationStatus", "source": "live",
    "values": [
        {"uri": "https://w3id.org/sciencelive/o/terms/Validated", "label": "validated"},
        {"uri": "https://w3id.org/sciencelive/o/terms/Contradicted",
         "label": "contradicted"},
    ],
}


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    """No network: fixed template, fixed vocabulary, no identifiers resolved."""
    monkeypatch.setattr(D, "template_fields", lambda step, live=True: SPEC)
    monkeypatch.setattr(D, "vocabulary", lambda name, live=True: STATUSES)
    monkeypatch.setattr(D, "resolve_doi", lambda doi: {
        "doi": doi, "resolves": True, "status": 200, "title": "A paper", "year": "2018"})
    monkeypatch.setattr(D, "wikidata_lookup", lambda q, limit=1: {"candidates": []})


def write(tmp_path, body: str, name: str = "05_outcome.md"):
    path = tmp_path / name
    path.write_text(body)
    return str(path)


def field(fid: str, value: str) -> str:
    return f"<!-- field: {fid} -->\n### {fid}\n\n```\n{value}\n```\n\n"


GOOD = (field("outcome", "mhw-days-increase")
        + field("study", "«URI of step 04 (FORRT Replication Study)»")
        + field("repo", "https://doi.org/10.5281/zenodo.21950033")
        + "<!-- field: validationStatus -->\n### status\n\n"
          "- [ ] contradicted\n- [x] validated\n\n"
        + field("conclusion", "The claim is validated."))


class TestHappyPath:
    def test_a_complete_draft_is_publishable(self, tmp_path):
        r = D.validate_draft(write(tmp_path, GOOD))
        assert r["publishable"] is True
        assert r["counts"]["error"] == 0

    def test_the_step_is_inferred_from_the_filename(self, tmp_path):
        assert D.validate_draft(write(tmp_path, GOOD))["step"] == "05_outcome"

    def test_an_unrecognisable_filename_asks_rather_than_guesses(self, tmp_path):
        with pytest.raises(ApiError, match="cannot tell which chain step"):
            D.validate_draft(write(tmp_path, GOOD, "notes.md"))

    def test_an_explicit_step_overrides_the_filename(self, tmp_path):
        assert D.validate_draft(
            write(tmp_path, GOOD, "notes.md"), step="05_outcome")["step"] == "05_outcome"


class TestPlaceholderKinds:
    """Only one of the three placeholder conventions is a problem. Reporting the
    other two as errors made the checker useless on real drafts."""

    def test_a_wizard_backreference_is_correct_not_an_error(self, tmp_path):
        r = D.validate_draft(write(tmp_path, GOOD))
        backref = [f for f in r["findings"] if f["check"] == "backreference"]
        assert backref and backref[0]["severity"] == "info"
        assert r["publishable"] is True

    def test_a_backreference_is_recognised_by_its_text_not_the_field_kind(self, tmp_path):
        """`work` on the CiTO template is an external_uri, yet the wizard fills
        it — so the «URI of step NN» convention is what identifies it."""
        body = GOOD.replace(
            field("repo", "https://doi.org/10.5281/zenodo.21950033"),
            field("repo", "«URI of step 05 (FORRT Replication Outcome)»"))
        r = D.validate_draft(write(tmp_path, body))
        assert r["publishable"] is True
        assert any(f["check"] == "backreference" and f["field"] == "repo"
                   for f in r["findings"])

    def test_a_release_token_warns_rather_than_blocking(self, tmp_path):
        """{{ZENODO_VERSION_DOI}} is substituted by the release workflow; it is
        expected while drafting."""
        body = GOOD.replace("https://doi.org/10.5281/zenodo.21950033",
                            "https://doi.org/{{ZENODO_VERSION_DOI}}")
        r = D.validate_draft(write(tmp_path, body))
        assert r["publishable"] is True
        token = [f for f in r["findings"] if f["check"] == "release-token"]
        assert token and token[0]["severity"] == "warning"
        assert "confirm the release has run" in token[0]["message"]

    def test_an_actually_unfilled_placeholder_is_an_error(self, tmp_path):
        body = GOOD.replace("The claim is validated.", "_not yet published_")
        r = D.validate_draft(write(tmp_path, body))
        assert r["publishable"] is False
        assert any(f["check"] == "placeholder" and f["severity"] == "error"
                   for f in r["findings"])


class TestFieldChecks:
    def test_a_field_id_not_on_the_template_is_an_error(self, tmp_path):
        r = D.validate_draft(write(tmp_path, GOOD + field("verdict", "validated")))
        bad = [f for f in r["findings"] if f["check"] == "field-exists"]
        assert bad and bad[0]["severity"] == "error"
        assert "verdict" in bad[0]["message"]

    def test_an_empty_required_field_is_an_error(self, tmp_path):
        body = GOOD.replace(field("conclusion", "The claim is validated."),
                            field("conclusion", ""))
        r = D.validate_draft(write(tmp_path, body))
        assert r["publishable"] is False
        assert any(f["check"] == "required" and f["field"] == "conclusion"
                   for f in r["findings"])

    def test_an_empty_optional_field_is_not(self, tmp_path):
        r = D.validate_draft(write(tmp_path, GOOD + field("note", "")))
        assert r["publishable"] is True

    def test_a_template_regex_is_enforced(self, tmp_path, monkeypatch):
        spec = {**SPEC, "fields": [
            {**f, "regex": r"10\.(\d)+/(\S)+"} if f["id"] == "repo" else f
            for f in SPEC["fields"]]}
        monkeypatch.setattr(D, "template_fields", lambda step, live=True: spec)
        body = GOOD.replace("https://doi.org/10.5281/zenodo.21950033", "not a doi")
        r = D.validate_draft(write(tmp_path, body))
        assert any(f["check"] == "constraint" and f["severity"] == "error"
                   for f in r["findings"])


class TestExtractionConventions:
    """A `<!-- field -->` marker runs to the next marker, so it can enclose an
    unrelated checkbox. The template's field kind decides which to read."""

    def test_a_stray_checkbox_does_not_hijack_a_free_text_field(self, tmp_path):
        body = ("<!-- field: repo -->\n### repository URL\n\n"
                "```\nhttps://doi.org/10.5281/zenodo.21950033\n```\n\n"
                "- [x] Quote whole text (less than 500 characters)\n"
                "- [ ] Quote start/end\n\n")
        fields = D.parse_draft(body, SPEC)
        assert fields["repo"]["value"] == "https://doi.org/10.5281/zenodo.21950033"
        assert fields["repo"]["source"] == "fenced-block"

    def test_a_choice_field_reads_the_tick_not_its_rationale_block(self, tmp_path):
        body = ("<!-- field: validationStatus -->\n### status\n\n"
                "- [ ] contradicted\n- [x] validated\n\n"
                "```\nsome explanatory text\n```\n")
        assert D.parse_draft(body, SPEC)["validationStatus"]["value"] == "validated"


class TestVocabulary:
    def test_a_valid_choice_is_reported_with_its_uri(self, tmp_path):
        r = D.validate_draft(write(tmp_path, GOOD))
        hit = [f for f in r["findings"] if f["check"] == "vocabulary"]
        assert hit and hit[0]["severity"] == "info"
        assert hit[0]["uri"].endswith("Validated")

    def test_a_value_the_form_would_reject_is_an_error(self, tmp_path):
        body = GOOD.replace("- [x] validated", "- [x] mostly validated")
        r = D.validate_draft(write(tmp_path, body))
        assert r["publishable"] is False
        bad = [f for f in r["findings"] if f["check"] == "vocabulary"]
        assert bad[0]["severity"] == "error"
        assert "contradicted" in bad[0]["message"]  # lists what IS allowed


class TestSkeletons:
    def test_an_unfilled_skeleton_is_reported_once_not_per_field(self, tmp_path):
        body = "".join(field(f["id"], "") for f in SPEC["fields"])
        r = D.validate_draft(write(tmp_path, body))
        skeleton = [f for f in r["findings"] if f["check"] == "skeleton"]
        assert len(skeleton) == 1
        assert skeleton[0]["severity"] == "warning"
        assert "not been drafted yet" in skeleton[0]["message"]
        # Not a wall of per-field errors.
        assert not [f for f in r["findings"] if f["check"] == "required"]


class TestIdentifiers:
    def test_an_unregistered_doi_blocks_publication(self, tmp_path, monkeypatch):
        monkeypatch.setattr(D, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False, "status": 404})
        r = D.validate_draft(write(tmp_path, GOOD))
        assert r["publishable"] is False
        assert any(f["check"] == "doi" and "NOT REGISTERED" in f["message"]
                   for f in r["findings"])

    def test_a_resolver_outage_warns_instead_of_blocking(self, tmp_path, monkeypatch):
        monkeypatch.setattr(D, "resolve_doi", lambda doi: {
            "doi": doi, "resolves": False, "status": 503})
        r = D.validate_draft(write(tmp_path, GOOD))
        assert r["publishable"] is True
        assert any(f["check"] == "doi" and f["severity"] == "warning"
                   for f in r["findings"])

    def test_a_placeholder_doi_in_prose_is_not_treated_as_a_doi(self, tmp_path):
        body = GOOD + "\nUse the version DOI `https://doi.org/10.5281/zenodo.<N>`.\n"
        r = D.validate_draft(write(tmp_path, body))
        assert not [f for f in r["findings"]
                    if f["check"] == "doi" and f.get("doi", "").endswith("zenodo")]


class TestDirectory:
    def test_a_directory_aggregates_every_draft(self, tmp_path):
        write(tmp_path, GOOD, "05_outcome.md")
        write(tmp_path, GOOD, "03_claim.md")
        (tmp_path / "README.md").write_text("not a draft")
        r = D.validate_drafts(str(tmp_path))
        assert r["draftsChecked"] == 2
        assert r["publishable"] is True

    def test_one_bad_draft_makes_the_set_unpublishable(self, tmp_path):
        write(tmp_path, GOOD, "05_outcome.md")
        write(tmp_path, GOOD + field("verdict", "x"), "03_claim.md")
        assert D.validate_drafts(str(tmp_path))["publishable"] is False

    def test_an_empty_directory_says_what_it_expected(self, tmp_path):
        with pytest.raises(ApiError, match="no recognisable drafts"):
            D.validate_drafts(str(tmp_path))


def test_a_file_without_field_markers_is_not_silently_passed(tmp_path):
    r = D.validate_draft(write(tmp_path, "# 05 — Outcome\n\nJust prose.\n"))
    assert r["publishable"] is False
    assert r["findings"][0]["check"] == "parse"
