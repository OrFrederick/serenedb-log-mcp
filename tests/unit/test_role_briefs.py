"""The Role playbooks (T7): the MCP prompts engineer, team_lead and manager, and get_role_brief.

MCP prompts are user-controlled and the Claude Code desktop app does not list them (#4), so the
model can fetch the same playbook itself with get_role_brief. Claude Code splits prompt arguments
on whitespace, so every argument is one optional token.
"""

from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, GetPromptResult, ListPromptsResult, TextContent

from serenedb_log_mcp.config import load_settings, set_settings
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.roles import NOT_MEASURED, answer_frame
from serenedb_log_mcp.roles.briefs import EXAMPLE_QUESTIONS, TITLES, role_brief
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META

# The playbooks ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_every_brief_carries_its_answer_frame_verbatim(role: Role) -> None:
    brief = role_brief(role)

    for line in answer_frame(role):
        assert line in brief
    assert f"role='{role.value}'" in brief
    assert TITLES[role] in brief


def test_the_three_briefs_differ() -> None:
    assert len({role_brief(role) for role in Role}) == 3


def test_the_engineer_brief_is_about_one_failure_with_context() -> None:
    brief = role_brief(Role.ENGINEER)

    for word in ("one failure", "just before", "search_logs", "get_context", "raw Log Entries"):
        assert word in brief, word


def test_the_team_lead_brief_is_about_recurring_problems_counts_trends_and_teams() -> None:
    brief = role_brief(Role.TEAM_LEAD)

    for word in ("recurring", "count_by", "trend", "Team", "baseline", "sample, not a frequency"):
        assert word in brief, word


def test_the_manager_brief_lists_what_is_not_measured_and_how_to_refuse() -> None:
    brief = role_brief(Role.MANAGER)

    for item in NOT_MEASURED:
        assert item in brief
    assert "not measurable from these logs" in brief
    assert "no raw Log Entries" in brief
    assert "measured frequency and affected Components" in brief


@pytest.mark.parametrize("role", list(Role))
def test_every_brief_keeps_log_entries_apart_from_incidents_and_cites_evidence(role: Role) -> None:
    brief = role_brief(role)

    assert "Log Entries are not Incidents" in brief
    assert "[E<n>]" in brief
    assert "Data Scope" in brief


# The MCP prompts -------------------------------------------------------------------------------------


def list_prompts(server: MCPServer) -> ListPromptsResult:
    async def listing() -> ListPromptsResult:
        async with Client(server, mode="legacy") as client:
            return await client.list_prompts()

    return anyio.run(listing)


def get_prompt(server: MCPServer, name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    async def getting() -> GetPromptResult:
        async with Client(server, mode="legacy") as client:
            return await client.get_prompt(name, arguments)

    return anyio.run(getting)


def prompt_text(result: GetPromptResult) -> str:
    (message,) = result.messages
    assert message.role == "user"
    assert isinstance(message.content, TextContent)
    return message.content.text


def test_the_server_offers_one_prompt_per_role() -> None:
    prompts = {prompt.name: prompt for prompt in list_prompts(create_server()).prompts}

    assert sorted(prompts) == ["engineer", "manager", "team_lead"]
    for prompt in prompts.values():
        assert prompt.title
        assert prompt.description


def test_every_prompt_argument_is_optional_and_described() -> None:
    prompts = list_prompts(create_server()).prompts

    expected = {
        "engineer": ["dataset", "focus"],
        "team_lead": ["dataset", "period"],
        "manager": ["dataset", "period"],
    }
    for prompt in prompts:
        arguments = prompt.arguments or []
        assert sorted(a.name for a in arguments) == sorted(expected[prompt.name])
        for argument in arguments:
            assert argument.required is False
            assert argument.description
            assert "token" in argument.description


@pytest.mark.parametrize("role", list(Role))
def test_a_prompt_without_arguments_is_the_brief_with_an_opening(role: Role) -> None:
    text = prompt_text(get_prompt(create_server(), role.value))

    assert role_brief(role) in text
    assert f"I am the {TITLES[role]}" in text
    for question in EXAMPLE_QUESTIONS[role]:
        assert question in text


def test_the_manager_prompt_scopes_the_dataset_and_the_period() -> None:
    text = prompt_text(get_prompt(create_server(), "manager", {"dataset": "bgl", "period": "30d"}))

    assert "dataset='bgl'" in text
    assert "time_from='last 30d'" in text


def test_a_period_may_also_be_written_with_last() -> None:
    text = prompt_text(get_prompt(create_server(), "team_lead", {"period": "last2w"}))

    assert "time_from='last 2w'" in text


def test_both_datasets_is_the_whole_base_corpus() -> None:
    text = prompt_text(get_prompt(create_server(), "team_lead", {"dataset": "both"}))

    assert "both Datasets" in text


def test_the_engineer_prompt_starts_from_the_focus() -> None:
    text = prompt_text(get_prompt(create_server(), "engineer", {"focus": "R27-M0-NA-C:J11-U11"}))

    assert "R27-M0-NA-C:J11-U11" in text


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("manager", {"dataset": "hdfs"}, "bgl, openstack or both"),
        ("team_lead", {"period": "a month"}, "30d"),
        ("engineer", {"focus": "two words"}, "one token"),
    ],
)
def test_a_bad_argument_is_an_actionable_error(name: str, arguments: dict[str, str], expected: str) -> None:
    async def failing() -> str:
        async with Client(create_server(), mode="legacy") as client:
            with pytest.raises(MCPError) as raised:
                await client.get_prompt(name, arguments)
            return raised.value.message

    assert expected in anyio.run(failing)


def test_a_server_pinned_to_a_role_offers_only_that_prompt() -> None:
    set_settings(load_settings({}, role="manager"))

    prompts = list_prompts(create_server()).prompts

    assert [prompt.name for prompt in prompts] == ["manager"]


# get_role_brief --------------------------------------------------------------------------------------


def call(server: MCPServer, **arguments: Any) -> CallToolResult:
    result = anyio.run(lambda: server.call_tool("get_role_brief", arguments))
    assert isinstance(result, CallToolResult)
    return result


def test_get_role_brief_is_registered_like_every_tool() -> None:
    tools = {tool.name: tool for tool in anyio.run(create_server().list_tools)}

    tool = tools["get_role_brief"]
    assert tool.title
    assert tool.meta == ALWAYS_LOAD_META
    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is True
    assert tool.description is not None
    for part in ("Purpose:", "Inputs:", "Outputs:"):
        assert part in tool.description
    assert tool.input_schema["required"] == ["role"]


@pytest.mark.parametrize("role", list(Role))
def test_get_role_brief_returns_the_same_text_as_the_prompt(role: Role) -> None:
    server = create_server()

    data = call(server, role=role.value).structured_content
    text = prompt_text(get_prompt(server, role.value))

    assert data is not None
    assert data["brief"] == role_brief(role)
    assert data["brief"] in text
    assert data["role"] == role.value
    assert data["title"] == TITLES[role]
    assert data["prompt"] == f"/mcp__serenedb-log__{role.value}"
    assert data["answer_frame"] == answer_frame(role)
    assert data["not_measured"] == (list(NOT_MEASURED) if role is Role.MANAGER else [])


def test_get_role_brief_writes_nothing_to_the_evidence_ledger() -> None:
    from serenedb_log_mcp.ledger import get_ledger

    call(create_server(), role="manager")

    assert get_ledger().last_id is None


def test_get_role_brief_honours_the_role_pin() -> None:
    set_settings(load_settings({}, role="manager"))

    with pytest.raises(ToolError, match="--role"):
        call(create_server(), role="engineer")
