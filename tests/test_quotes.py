"""Quote verification: it must accept faithful quotations and reject altered ones.

The hard case is not the paraphrase — it is the quotation that differs by one
digit. Those score ~0.91 similarity against the source and are exactly what a
signed nanopublication must not carry, so several tests below pin that boundary.
"""
from __future__ import annotations

import pytest

from forrt_replication_mcp.quotes import MIN_QUOTE_CHARS, QuoteError, verify_quote

# The real sentence from Oliver et al. 2018 that the marine-heatwave chain quotes.
SENTENCE = ("The increases in frequency and duration metrics translate to 30 additional "
            "marine heatwave days per year by the end of the 35-year period "
            "(p < 0.01; based on a linear trend) from a baseline level of about "
            "25 days in the 1980s (Fig. 2).")


@pytest.fixture
def clean_pdf(write_pdf):
    """A PDF whose text stream holds the sentence verbatim on one line."""
    return write_pdf([SENTENCE])


class TestExactMatch:
    def test_verbatim_text_matches_exactly(self, clean_pdf):
        result = verify_quote(str(clean_pdf), SENTENCE)
        assert result["found"] is True
        assert result["match"] == "exact"
        assert result["page"] == 1
        assert result["transforms"] == []

    def test_offsets_locate_the_quotation_in_the_page(self, clean_pdf):
        result = verify_quote(str(clean_pdf), SENTENCE)
        assert result["char_end"] - result["char_start"] == len(SENTENCE)
        assert result["matched_text"] == SENTENCE

    def test_evidence_is_reproducible(self, clean_pdf):
        """A reviewer must be able to re-run this against the same bytes."""
        first, second = (verify_quote(str(clean_pdf), SENTENCE) for _ in range(2))
        assert first == second
        assert len(first["pdf_sha256"]) == 64
        assert first["pdf_pages"] == 1


class TestNormalizedMatch:
    def test_line_broken_quotation_still_matches(self, write_pdf):
        pdf = write_pdf(["The increases in frequency and duration metrics",
                         "translate to 30 additional marine heatwave days."])
        result = verify_quote(
            str(pdf),
            "The increases in frequency and duration metrics translate to 30 "
            "additional marine heatwave days.")
        assert result["match"] == "normalized"
        assert "collapse-whitespace" in result["transforms"]

    def test_hyphenation_across_a_line_break_is_repaired(self, write_pdf):
        pdf = write_pdf(["Globally averaged marine heat-",
                         "wave days rose over the record."])
        result = verify_quote(
            str(pdf), "Globally averaged marine heatwave days rose over the record.")
        assert result["found"] is True
        assert "join-hyphenated-linebreaks" in result["transforms"]

    def test_typographic_punctuation_is_canonicalised(self, write_pdf):
        """Helvetica's StandardEncoding renders byte 0x27 as U+2019, so a PDF
        written with a straight apostrophe extracts with a curly one. Someone
        retyping the sentence types the straight form; both must match."""
        from pypdf import PdfReader

        pdf = write_pdf(["The author's claim about heatwave days is tested here."])
        assert "’" in PdfReader(str(pdf)).pages[0].extract_text(), (
            "fixture no longer reproduces the encoding artifact under test")

        result = verify_quote(
            str(pdf), "The author's claim about heatwave days is tested here.")
        assert result["found"] is True
        assert "typographic-punctuation" in result["transforms"]


class TestExtractionTolerantMatch:
    """Artifacts pypdf leaves in real papers: a dropped hyphen and a space that
    appears after an opening bracket. Both occur in Oliver et al. 2018."""

    def test_dropped_hyphen_and_bracket_spacing_are_tolerated(self, write_pdf):
        pdf = write_pdf(["days per year by the end of the 35year period ( p < 0.01;",
                         "based on a linear trend) from a baseline level."])
        result = verify_quote(
            str(pdf),
            "days per year by the end of the 35-year period (p < 0.01; based on a "
            "linear trend) from a baseline level.")
        assert result["found"] is True
        assert result["match"] == "extraction_tolerant"
        assert {"ignore-hyphens", "tighten-punctuation-spacing"} <= set(result["transforms"])

    def test_matched_text_shows_the_paper_not_the_match_key(self, write_pdf):
        """The caller must be able to read what the PDF says, hyphens and all."""
        pdf = write_pdf(["the end of the 35year period ( p < 0.01) from a baseline."])
        result = verify_quote(
            str(pdf), "the end of the 35-year period (p < 0.01) from a baseline.")
        assert "35year" in result["matched_text"]


class TestRejection:
    """Every tier canonicalises formatting only. Content changes must not pass."""

    @pytest.mark.parametrize("altered,label", [
        (SENTENCE.replace("30 additional", "40 additional"), "changed headline number"),
        (SENTENCE.replace("about 25 days", "about 35 days"), "changed baseline number"),
        (SENTENCE.replace("p < 0.01", "p < 0.05"), "changed significance level"),
        (SENTENCE.replace(" (p < 0.01; based on a linear trend)", ""), "dropped clause"),
        (SENTENCE.replace("increases", "decreases"), "reversed direction"),
    ])
    def test_altered_quotations_are_rejected(self, clean_pdf, altered, label):
        result = verify_quote(str(clean_pdf), altered)
        assert result["found"] is False, f"{label} was accepted"
        assert result["match"] == "not_found"

    def test_a_near_miss_is_still_a_miss(self, clean_pdf):
        """One changed digit scores ~0.9 similarity and must still fail."""
        result = verify_quote(str(clean_pdf), SENTENCE.replace("30 additional", "40 additional"))
        assert result["closest"]["similarity"] > 0.85
        assert result["found"] is False

    def test_fluent_paraphrase_is_rejected(self, clean_pdf):
        result = verify_quote(
            str(clean_pdf),
            "Marine heatwave days increased by roughly 30 per year over the 35-year "
            "record, up from about 25 days during the 1980s.")
        assert result["found"] is False

    def test_rejection_shows_what_the_pdf_actually_says(self, clean_pdf):
        result = verify_quote(str(clean_pdf), SENTENCE.replace("30 additional", "40 additional"))
        assert "30 additional" in result["closest"]["text_in_pdf"]
        assert result["closest"]["page"] == 1

    def test_text_from_a_different_paper_is_rejected(self, clean_pdf):
        result = verify_quote(
            str(clean_pdf),
            "Attention mechanisms in the U-Net architecture enabled the model to "
            "focus on burnt areas.")
        assert result["found"] is False
        assert result["closest"]["similarity"] < 0.5


class TestGuards:
    def test_missing_file_is_an_error_not_a_false_negative(self):
        with pytest.raises(QuoteError, match="no such file"):
            verify_quote("/nonexistent/paper.pdf", SENTENCE)

    def test_short_strings_are_refused_rather_than_matched_by_accident(self, clean_pdf):
        with pytest.raises(QuoteError, match="refusing to verify"):
            verify_quote(str(clean_pdf), "The increases")

    def test_the_threshold_is_the_documented_one(self, clean_pdf):
        assert MIN_QUOTE_CHARS == 20
        with pytest.raises(QuoteError):
            verify_quote(str(clean_pdf), "x" * (MIN_QUOTE_CHARS - 1))
