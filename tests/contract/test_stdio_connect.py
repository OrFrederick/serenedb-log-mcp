"""Claude can connect using our instructions: the `.mcp.json` command starts a working stdio server.

Claude Code starts `serenedb-log` with the command in `.mcp.json`. This runs exactly that command as
a separate process over stdio, the way Claude Code does, and checks the handshake (server name and
instructions), the tool listing (the same tools as the in-process server, so nothing is lost to an
import error or a stray print on stdout) and the three Role prompts. Listing needs no database;
tests/db/contract/test_connect_db.py makes a call through the same command.
"""

from __future__ import annotations

import shutil

import anyio
import pytest
from contract_calls import CORE_TOOLS
from contract_session import mcp_json_entry, mcp_json_parameters
from mcp import Client

from serenedb_log_mcp.server import SERVER_NAME, create_server

ROLE_PROMPTS = {"engineer", "team_lead", "manager"}


def in_process_tools() -> set[str]:
    async def listing() -> set[str]:
        async with Client(create_server(), mode="legacy") as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    return anyio.run(listing)


def test_mcp_json_registers_the_server_over_stdio_under_its_own_name() -> None:
    entry = mcp_json_entry()

    assert entry["type"] == "stdio"
    assert entry["command"] == "uv", "the documented route runs the server through uv from a clean checkout"
    assert entry["args"][-1] == "serenedb-log-mcp"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_mcp_json_command_starts_a_server_with_every_tool_and_role_prompt() -> None:
    async def handshake() -> tuple[str, str, set[str], set[str]]:
        async with Client(mcp_json_parameters()) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
            return client.server_info.name, client.instructions or "", tools, prompts

    name, instructions, tools, prompts = anyio.run(handshake)

    assert name == SERVER_NAME
    assert "verify_answer" in instructions, "the server instructions reach the client"
    assert tools >= CORE_TOOLS, f"missing over stdio: {sorted(CORE_TOOLS - tools)}"
    assert tools == in_process_tools()
    assert prompts >= ROLE_PROMPTS
