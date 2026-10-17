"""The Roles differ in substance (T7, #4), over MCP, through an in-process client.

The same question through the three Roles yields different results, in what a client actually
receives (`structuredContent`, which is all Claude Code shows the model): the Engineer gets the raw
Log Entries, the Team Lead three samples, the Manager none, with the same exact totals, each with
its own answer frame, and only the Manager with the not-measured list. Evidence handed between
Roles by its id is shaped for the Role that fetches it.

The contract conftest installs the identity Role hook for isolation; these tests install the
product's Role shaping, which is the toolkit default.
"""

import json
from typing import Any

import anyio
import jsonschema
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from sample_tool import SEARCH_ROWS, SEARCH_SUMMARY, SEARCH_TOTAL, build_sample_server, register_sample_search

from serenedb_log_mcp import roles, toolkit
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.roles import NOT_MEASURED, answer_frame


@pytest.fixture(autouse=True)
def role_shaping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.DEFAULT_ROLE_HOOK)
    assert toolkit.DEFAULT_ROLE_HOOK is roles.shape_for_role


def server() -> MCPServer:
    """The product server, the sample count tool and a sample retrieval tool."""
    built = build_sample_server()
    register_sample_search(built)
    return built


def session(calls: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Run the calls in order in one client session; return each structuredContent, schema-checked."""

    async def run() -> list[dict[str, Any]]:
        async with Client(server(), mode="legacy") as client:
            schemas = {tool.name: tool.output_schema for tool in (await client.list_tools()).tools}
            out = []
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                assert isinstance(result, CallToolResult)
                assert result.is_error is False, result.content
                data = result.structured_content
                assert data is not None
                jsonschema.validate(data, schemas[name])
                assert json.loads(result.content[0].text) == data  # the text mirrors it
                out.append(data)
            return out

    return anyio.run(run)


def test_the_same_question_yields_different_results_for_each_role() -> None:
    engineer, team_lead, manager = session(
        [("sample_search", {"role": role.value}) for role in (Role.ENGINEER, Role.TEAM_LEAD, Role.MANAGER)]
    )

    # Different Evidence: every row, three samples, none.
    assert engineer["rows"] == SEARCH_ROWS
    assert team_lead["rows"] == SEARCH_ROWS[:3]
    assert manager["rows"] == []
    # The same exact totals and facets for all three: frequency is never taken away.
    for result in (engineer, team_lead, manager):
        assert result["total_matches"] == SEARCH_TOTAL
        assert result["summary"] == SEARCH_SUMMARY
    # A different answer frame for each Role, and the not-measured list for the Manager only.
    assert engineer["answer_frame"] == answer_frame(Role.ENGINEER)
    assert team_lead["answer_frame"] == answer_frame(Role.TEAM_LEAD)
    assert manager["answer_frame"] == answer_frame(Role.MANAGER)
    assert engineer["not_measured"] == team_lead["not_measured"] == []
    assert manager["not_measured"] == list(NOT_MEASURED)
    # The notes say why the rows differ.
    assert "withheld" in manager["notes"][0]
    assert "sample, not a frequency" in team_lead["notes"][0]


def test_evidence_handed_from_the_engineer_is_shaped_for_the_role_that_fetches_it() -> None:
    produced, for_manager, for_team_lead, for_engineer = session(
        [
            ("sample_search", {"role": "engineer"}),
            ("get_evidence", {"role": "manager", "evidence_id": "E1"}),
            ("get_evidence", {"role": "team_lead", "evidence_id": "E1"}),
            ("get_evidence", {"role": "engineer", "evidence_id": "E1"}),
        ]
    )

    assert produced["evidence_id"] == "E1"
    assert for_manager["rows"] == []
    assert for_manager["total_matches"] == SEARCH_TOTAL
    assert for_manager["not_measured"] == list(NOT_MEASURED)
    assert any("returned to role 'manager'" in note for note in for_manager["notes"])
    assert len(for_team_lead["rows"]) == 3
    assert for_engineer["rows"] == SEARCH_ROWS


def test_aggregation_evidence_reaches_the_manager_whole() -> None:
    engineer, manager = session(
        [("sample_count", {"role": "engineer"}), ("sample_count", {"role": "manager"})]
    )

    assert manager["rows"] == engineer["rows"]
    assert manager["answer_frame"] != engineer["answer_frame"]
    assert manager["not_measured"] == list(NOT_MEASURED)


def test_the_role_briefs_differ_and_match_the_answer_frames() -> None:
    briefs = session([("get_role_brief", {"role": role.value}) for role in Role])

    assert len({brief["brief"] for brief in briefs}) == 3
    for role, brief in zip(Role, briefs, strict=True):
        assert brief["answer_frame"] == answer_frame(role)
