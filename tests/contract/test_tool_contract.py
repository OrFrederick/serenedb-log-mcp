"""L0 contract (T15): every tool in `tools/list` is something Claude can choose and use correctly.

What the Best MCP Server judges look at, checked on the listing a client really receives, for
whatever tools the server offers, so a tool that lands later is checked without editing this file
(the rules are in tests/support/tool_contract.py, and their self-tests in test_contract_checks.py):

- a required `role` from the three-Role enum, a title and a description that states the purpose,
  names every input and summarises the outputs (the model never sees the output schema);
- the read-only annotations (only `create_index` and `drop_index` may write) and the
  `anthropic/alwaysLoad` meta, without which Claude Code hides the tool behind ToolSearch;
- enums for closed sets, declared bounds on every input, row limits that match plan section 2.3 and
  are stated in the description, and an output schema;
- representative inputs for the db contract tests (tests/db/contract) that validate against the
  input schema.
"""

from __future__ import annotations

import functools
import re

import anyio
import jsonschema
import pytest
from contract_calls import CORE_TOOLS, SERVER_PREFIX, TOOL_NAME_LIMIT, representative_calls
from mcp import Client
from mcp_types import Tool
from tool_contract import CHECKS, ROLES

from serenedb_log_mcp.server import SERVER_NAME, create_server


@functools.cache
def listing() -> dict[str, Tool]:
    """The product server's tools/list, as an in-process client receives it."""

    async def tools_list() -> list[Tool]:
        async with Client(create_server(), mode="legacy") as client:
            return (await client.list_tools()).tools

    return {tool.name: tool for tool in anyio.run(tools_list)}


TOOL_NAMES = sorted(listing())


def test_the_listing_offers_every_core_tool() -> None:
    missing = CORE_TOOLS - set(TOOL_NAMES)
    assert not missing, f"missing from tools/list: {sorted(missing)}"


def test_tool_names_are_snake_case_and_fit_the_claude_tool_name_limit() -> None:
    assert f"mcp__{SERVER_NAME}__" == SERVER_PREFIX
    for name in TOOL_NAMES:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name), name
        assert len(SERVER_PREFIX + name) <= TOOL_NAME_LIMIT, f"{SERVER_PREFIX}{name} is too long for Claude"


def test_titles_are_unique() -> None:
    titles = [listing()[name].title for name in TOOL_NAMES]
    assert len(set(titles)) == len(titles), titles


@pytest.mark.parametrize("rule", sorted(CHECKS))
@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_keeps_every_contract_rule(name: str, rule: str) -> None:
    problems = CHECKS[rule](listing()[name])

    assert problems == [], f"{name} breaks the {rule} rule: {'; '.join(problems)}"


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_representative_inputs_exist_and_validate_against_the_input_schema(name: str) -> None:
    schema = listing()[name].input_schema
    calls = representative_calls(name, schema)

    assert calls, (
        f"{name} has a required parameter whose value cannot be derived from its schema; add its "
        "representative calls to REPRESENTATIVE_CALLS in tests/support/contract_calls.py"
    )
    for arguments in calls:
        for role in ROLES:
            jsonschema.validate({**arguments, "role": role}, schema)
