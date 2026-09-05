"""The MCP wrapper layer.

Every other test imports the modules directly. These call through
`mcp.call_tool`, the way an agent actually reaches them, so a wrapper bug —
a mistyped parameter passthrough, an exception that escapes instead of becoming
`ok: false` — cannot hide behind a green suite.

Hermetic: only offline paths (`live=False`) and local fixtures.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from forrt_research_mcp.server import mcp

EXPECTED_TOOLS = {
    "constellation", "constellation_raw", "list_schemas", "prior_work",
    "resolve_doi", "template_fields", "validate_chain_draft", "validate_draft",
    "validate_drafts", "verify_chain", "verify_quote", "vocabulary",
    "wikidata_lookup",
}


def call(tool: str, **kwargs) -> dict:
    """Invoke a tool through the MCP layer and return its payload."""
    result = asyncio.run(mcp.call_tool(tool, kwargs))
    if isinstance(result, tuple) and len(result) > 1 and result[1] is not None:
        return result[1]
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.fixture
def quote_pdf(write_pdf):
    return str(write_pdf(["A sentence long enough to satisfy the minimum length."]))


class TestRegistration:
    def test_every_expected_tool_is_registered(self):
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        assert names == EXPECTED_TOOLS

    def test_the_server_is_named_for_all_three_study_shapes(self):
        """Not `forrt-replication` — it serves reproduction, replication and
        research that starts from scratch."""
        assert mcp.name == "forrt-research"

    def test_every_tool_documents_itself(self):
        """The docstring is the agent's only guide to when to call a tool."""
        for tool in asyncio.run(mcp.list_tools()):
            assert tool.description and len(tool.description) > 120, tool.name

    @pytest.mark.parametrize("tool,required", [
        ("constellation", ["uri"]),
        ("prior_work", ["uri"]),
        ("verify_quote", ["pdf_path", "quotation"]),
        ("template_fields", ["step"]),
        ("vocabulary", ["name"]),
        ("resolve_doi", ["doi"]),
        ("wikidata_lookup", ["query"]),
        ("validate_draft", ["path"]),
        ("validate_drafts", ["directory"]),
        ("validate_chain_draft", ["path"]),
        ("verify_chain", ["published_path"]),
    ])
    def test_required_parameters_are_what_the_docs_promise(self, tool, required):
        """The skills in forrt-replication-template call these by keyword; a
        renamed parameter breaks them silently."""
        spec = next(t for t in asyncio.run(mcp.list_tools()) if t.name == tool)
        assert sorted(spec.inputSchema.get("required") or []) == sorted(required)


class TestSuccessPayloads:
    def test_every_success_carries_ok_true(self):
        assert call("list_schemas")["ok"] is True

    def test_list_schemas_needs_no_arguments_or_network(self):
        result = call("list_schemas")
        assert len(result["steps"]) == 10
        assert "pico_question_type" in result["vocabularies"]

    def test_template_fields_accepts_a_step_alias(self):
        result = call("template_fields", step="outcome", live=False)
        assert result["ok"] is True and result["step"] == "05_outcome"

    def test_vocabulary_passes_live_through(self):
        """`live` must reach the implementation — otherwise every offline call
        would hit the network."""
        result = call("vocabulary", name="cito_relation", live=False)
        assert result["source"] == "unavailable-offline"

    def test_verify_quote_reaches_the_implementation(self, quote_pdf):
        result = call("verify_quote", pdf_path=quote_pdf,
                      quotation="A sentence long enough to satisfy the minimum length.")
        assert result["ok"] is True and result["found"] is True


class TestFailuresBecomeData:
    """A tool must never raise into the client: the agent has to be able to
    relay what broke, verbatim."""

    @pytest.mark.parametrize("tool,kwargs,expected", [
        ("template_fields", {"step": "nonsense"}, "Known steps"),
        ("vocabulary", {"name": "nope"}, "Known:"),
        ("resolve_doi", {"doi": "not-a-doi"}, "DOI-shaped"),
        ("wikidata_lookup", {"query": "x", "expected_type": "P31"}, "QID"),
        ("verify_quote", {"pdf_path": "/nope.pdf", "quotation": "x" * 40}, "no such file"),
        ("validate_draft", {"path": "/nope.md"}, "no such draft"),
        ("validate_drafts", {"directory": "/nope"}, "not a directory"),
        ("validate_chain_draft", {"path": "/nope.json"}, "no chain draft"),
        ("verify_chain", {"published_path": "/nope.md"}, "no ledger"),
    ])
    def test_a_bad_argument_returns_ok_false_with_the_real_message(
            self, tool, kwargs, expected):
        result = call(tool, **kwargs)
        assert result["ok"] is False
        assert expected in result["error"]
        assert result["errorType"]

    def test_the_length_guard_fires_only_once_the_file_exists(self, quote_pdf):
        """Ordering matters: a missing file and a too-short quotation are
        different problems, and reporting the wrong one sends the caller
        looking in the wrong place."""
        missing = call("verify_quote", pdf_path="/nope.pdf", quotation="short")
        assert "no such file" in missing["error"]
        short = call("verify_quote", pdf_path=quote_pdf, quotation="short")
        assert "refusing to verify" in short["error"]

    def test_an_unknown_verify_chain_mode_lists_the_real_ones(self, tmp_path):
        ledger = tmp_path / "PUBLISHED.md"
        ledger.write_text("| 05 | X | https://w3id.org/np/RA" + "x" * 30 + " | 2026 |\n")
        result = call("verify_chain", published_path=str(ledger), mode="primary")
        assert result["ok"] is False and "expected one of" in result["error"]
