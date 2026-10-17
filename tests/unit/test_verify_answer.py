"""`verify_answer(role, draft)` (T12): the answer checks as a tool, over the Evidence Ledger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from sample_tool import build_sample_server, register_sample_search
from verify_ledger import ledger_records

from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.evidence import Evidence, Role, RoleParam
from serenedb_log_mcp.ledger import Ledger, get_ledger
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META, role_tool

# sample_count stores E1: KERNDTLB 152,734, KERNSTOR 63,491, APPSEV 49,651, entries_total 265,876.
GOOD = """\
## Observed Facts
- KERNDTLB has 152,734 Log Entries, KERNSTOR 63,491 and APPSEV 49,651 [E1].
- Together they are 265,876 Log Entries [E1].

## Hypotheses
- One node may be looping on the same fault.

## Unknowns
- Which node wrote them: this count has no node breakdown.

## Next Step
- Run count_by with group_by='node' for KERNDTLB.
"""

BAD = GOOD.replace("152,734 Log Entries", "about 150k Log Entries").replace(
    "265,876 Log Entries [E1]", "265,876 Log Entries [E7]"
)


def call(server: MCPServer, name: str, **arguments: Any) -> CallToolResult:
    async def one_call() -> CallToolResult:
        async with Client(server, mode="legacy") as client:
            result = await client.call_tool(name, arguments)
            assert isinstance(result, CallToolResult)
            return result

    return anyio.run(one_call)


def verify(server: MCPServer, role: str, draft: str) -> dict[str, Any]:
    result = call(server, "verify_answer", role=role, draft=draft)
    assert result.is_error is False, result.content
    assert result.structured_content is not None
    assert json.loads(result.content[0].text) == result.structured_content  # the text mirrors it
    return result.structured_content


@pytest.fixture
def server() -> MCPServer:
    return build_sample_server()


def test_verify_answer_is_registered_like_every_tool() -> None:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}

    tool = tools["verify_answer"]
    assert tool.title
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert sorted(tool.input_schema["required"]) == ["draft", "role"]
    assert tool.output_schema is not None
    assert set(tool.output_schema["properties"]) == {
        "ok",
        "role",
        "summary",
        "problems",
        "checked_numbers",
        "cited_evidence",
        "notes",
    }


def test_the_description_says_to_call_it_before_every_final_answer_and_fix_every_problem() -> None:
    tool = next(t for t in anyio.run(create_server().list_tools) if t.name == "verify_answer")

    assert tool.description is not None
    description = " ".join(tool.description.split())
    assert "before every final answer" in description
    assert "fix every problem" in description
    assert "Inputs:" in description
    assert "Outputs:" in description


def test_a_correct_draft_passes(server: MCPServer) -> None:
    call(server, "sample_count", role="team_lead")

    data = verify(server, "engineer", GOOD)

    assert data["ok"] is True
    assert data["problems"] == []
    assert data["cited_evidence"] == ["E1"]
    assert {n["text"] for n in data["checked_numbers"]} == {"152,734", "63,491", "49,651", "265,876"}
    assert data["summary"].startswith("OK")


def test_a_broken_draft_comes_back_as_problems_not_as_an_error(server: MCPServer) -> None:
    call(server, "sample_count", role="team_lead")

    data = verify(server, "engineer", BAD)

    assert data["ok"] is False
    kinds = [p["kind"] for p in data["problems"]]
    assert kinds == ["unknown_evidence", "rounded_number"]
    for problem in data["problems"]:
        assert problem["text"]
        assert problem["hint"]
        assert problem["section"] == "Observed Facts"
    assert "2 problems" in data["summary"]
    assert "verify_answer again" in data["summary"]


@pytest.mark.parametrize(
    "draft", ["", "no sections at all", "[E", "## Observed Facts\n" + "1,234 [E1] " * 500]
)
def test_a_malformed_draft_never_fails_the_call(server: MCPServer, draft: str) -> None:
    data = verify(server, "manager", draft)

    assert data["ok"] is False


def test_verifying_adds_nothing_to_the_ledger(server: MCPServer) -> None:
    call(server, "sample_count", role="team_lead")

    verify(server, "engineer", GOOD)
    verify(server, "engineer", BAD)

    assert [record["evidence_id"] for record in get_ledger().all()] == ["E1"]


def test_evidence_from_earlier_sessions_on_the_same_ledger_path_resolves(ledger_path: Path) -> None:
    earlier = Ledger(ledger_path)
    for record in ledger_records()[:3]:  # E1 count_by, E2 search_logs, E3 list_incidents
        with earlier.appending() as slot:
            slot.store(record)

    draft = GOOD.replace(
        "- Together they are 265,876 Log Entries [E1].",
        "- 152,182 of them are one Incident on one node [E3].",
    )
    data = verify(create_server(), "team_lead", draft)

    assert data["ok"] is True, data["problems"]
    assert data["cited_evidence"] == ["E1", "E3"]


def test_evidence_appended_by_another_process_after_startup_resolves(ledger_path: Path) -> None:
    server = create_server()
    verify(server, "engineer", GOOD)  # the ledger is empty: E1 does not resolve yet

    with Ledger(ledger_path).appending() as slot:
        slot.store(ledger_records()[0] | {"evidence_id": slot.evidence_id})

    draft = GOOD.replace(
        "KERNSTOR 63,491 and APPSEV 49,651 [E1]",
        "KERNSTOR 63,491 and APPSEV 49,651, 43.83% for KERNDTLB [E1]",
    ).replace("- Together they are 265,876 Log Entries [E1].", "- The top 3 hold 265,876 Log Entries [E1].")
    assert verify(server, "engineer", draft)["ok"] is True


def test_the_manager_rules_apply_through_the_tool(server: MCPServer) -> None:
    register_sample_search(server)
    call(server, "sample_search", role="engineer")  # E1: raw Log Entries

    draft = GOOD.replace(
        "KERNDTLB has 152,734 Log Entries, KERNSTOR 63,491 and APPSEV 49,651 [E1].",
        "The search matched 152,182 Log Entries [E1].",
    ).replace("- Together they are 265,876 Log Entries [E1].\n", "")
    draft = draft.replace(
        "## Unknowns\n",
        "## Unknowns\n- Cost, downtime and customer impact: these logs do not measure them.\n",
    )
    kinds = [p["kind"] for p in verify(server, "manager", draft)["problems"]]

    assert "manager_raw_line_evidence" in kinds


def test_the_side_by_side_check_waits_for_an_incident_tool(server: MCPServer) -> None:
    call(server, "sample_count", role="team_lead")

    data = verify(server, "team_lead", GOOD)

    assert data["ok"] is True
    assert any("list_incidents" in note for note in data["notes"])


def test_the_side_by_side_check_applies_once_an_incident_tool_is_offered(server: MCPServer) -> None:
    @role_tool(server, title="Stand-in for T27", output_model=Evidence, name="list_incidents")
    def list_incidents(role: RoleParam) -> Evidence:
        """Stand-in for list_incidents (T27). Inputs: role. Outputs: nothing useful."""
        raise NotImplementedError

    call(server, "sample_count", role="team_lead")

    kinds = [p["kind"] for p in verify(server, "team_lead", GOOD)["problems"]]

    assert kinds == ["log_entries_without_incidents"]


def test_verify_answer_honours_the_role_pin(server: MCPServer) -> None:
    set_settings(load_settings({}, role=Role.MANAGER))

    result = call(server, "verify_answer", role="engineer", draft=GOOD)

    assert result.is_error is True
    assert "--role manager" in result.content[0].text
