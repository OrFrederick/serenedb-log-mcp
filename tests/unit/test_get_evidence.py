"""`get_evidence(role, evidence_id)`: fetch stored Evidence again, shaped for the Role."""

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult
from sample_tool import SAMPLE_ROWS, register_sample_tools

from serenedb_log_mcp.evidence import Evidence
from serenedb_log_mcp.ledger import Ledger, get_ledger
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META, set_role_hook


@pytest.fixture
def server() -> MCPServer:
    """The product server (which brings get_evidence) plus the sample Evidence tool."""
    server = create_server()
    register_sample_tools(server)
    return server


def call(server: MCPServer, name: str, **arguments: Any) -> CallToolResult:
    result = anyio.run(lambda: server.call_tool(name, arguments))
    assert isinstance(result, CallToolResult)
    return result


def envelope(result: CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None
    return result.structured_content


def test_get_evidence_is_registered_on_the_product_server() -> None:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}

    tool = tools["get_evidence"]
    assert tool.title
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.description is not None
    assert "Outputs:" in tool.description
    assert sorted(tool.input_schema["required"]) == ["evidence_id", "role"]


def test_the_stored_record_comes_back_unchanged(server: MCPServer) -> None:
    stored = envelope(call(server, "sample_count", role="team_lead"))

    again = envelope(call(server, "get_evidence", role="team_lead", evidence_id="E1"))

    assert again == stored
    assert again["rows"] == SAMPLE_ROWS


def test_the_text_block_mirrors_the_structured_content(server: MCPServer) -> None:
    call(server, "sample_count", role="engineer")

    result = call(server, "get_evidence", role="engineer", evidence_id="E1")

    assert len(result.content) == 1
    assert json.loads(result.content[0].text) == envelope(result)


def test_fetching_evidence_adds_nothing_to_the_ledger(server: MCPServer) -> None:
    call(server, "sample_count", role="engineer")

    call(server, "get_evidence", role="engineer", evidence_id="E1")

    assert [record["evidence_id"] for record in get_ledger().all()] == ["E1"]


def test_the_record_comes_back_through_the_role_hook(server: MCPServer) -> None:
    call(server, "sample_count", role="team_lead")

    # T7 installs the real shaping; here it stands for "the Manager gets no raw rows".
    set_role_hook(lambda evidence: evidence.model_copy(update={"rows": []}))
    data = envelope(call(server, "get_evidence", role="manager", evidence_id="E1"))

    assert data["rows"] == []
    assert data["total_matches"] == len(SAMPLE_ROWS)
    assert get_ledger().get("E1")["rows"] == SAMPLE_ROWS


def test_evidence_from_an_earlier_session_stays_addressable(server: MCPServer, ledger_path: Path) -> None:
    earlier = Ledger(ledger_path)
    with earlier.appending() as slot:
        slot.store(
            Evidence(
                evidence_id=slot.evidence_id,
                tool="count_by",
                role="manager",
                data_scope={"datasets": ["bgl"]},
                total_matches=348_460,
                returned=3,
                truncated=True,
                query_ms=126.2,
            ).model_dump(mode="json")
        )

    data = envelope(call(server, "get_evidence", role="manager", evidence_id="E1"))

    assert data["tool"] == "count_by"
    assert data["total_matches"] == 348_460


@pytest.mark.parametrize("written", ["E1", "e1", " E1 ", "[E1]", "1"])
def test_the_id_is_read_the_way_the_agent_writes_it(server: MCPServer, written: str) -> None:
    call(server, "sample_count", role="engineer")

    data = envelope(call(server, "get_evidence", role="engineer", evidence_id=written))

    assert data["evidence_id"] == "E1"


def test_an_unknown_id_names_the_range_that_exists(server: MCPServer) -> None:
    call(server, "sample_count", role="engineer")
    call(server, "sample_count", role="engineer")

    with pytest.raises(ToolError, match="E1 to E2") as raised:
        call(server, "get_evidence", role="engineer", evidence_id="E9")

    assert "E9" in str(raised.value)


def test_an_empty_ledger_says_what_to_do_first(server: MCPServer) -> None:
    with pytest.raises(ToolError, match="empty") as raised:
        call(server, "get_evidence", role="engineer", evidence_id="E1")

    assert "evidence_id" in str(raised.value)


def test_a_record_this_server_can_no_longer_read_fails_loudly(server: MCPServer, ledger_path: Path) -> None:
    ledger_path.write_text(json.dumps({"evidence_id": "E1", "tool": "count_by"}) + "\n", encoding="utf-8")

    with pytest.raises(ToolError, match="E1") as raised:
        call(server, "get_evidence", role="engineer", evidence_id="E1")

    assert "validate" in str(raised.value)
