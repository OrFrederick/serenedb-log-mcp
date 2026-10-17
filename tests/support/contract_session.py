"""MCP client sessions for the contract tests (T15).

`run_session` lists the tools and makes a series of calls in one in-process session, so the calls
share one Evidence Ledger, and returns what a client receives: the listing and, per call, the
`structuredContent` on success or the error text on failure. `mcp_json_parameters` is the stdio
command Claude Code runs for this server, read from `.mcp.json`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import Client, StdioServerParameters
from mcp_types import CallToolResult, Tool

from serenedb_log_mcp.server import SERVER_NAME, create_server

ROOT = Path(__file__).parents[2]


def mcp_json_entry() -> dict[str, Any]:
    """This server's entry in `.mcp.json`, the registration Claude Code reads."""
    entry: dict[str, Any] = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"][SERVER_NAME]
    return entry


def mcp_json_parameters() -> StdioServerParameters:
    """The `.mcp.json` command, run from the repository root with this process's environment
    (DATABASE_URL, SLM_SCHEMA, ...), but without LEDGER_PATH, so its Evidence Ledger is in memory."""
    entry = mcp_json_entry()
    env = {key: value for key, value in os.environ.items() if key != "LEDGER_PATH"}
    return StdioServerParameters(command=entry["command"], args=entry["args"], env=env, cwd=ROOT)


@dataclass(frozen=True)
class Outcome:
    """One call and what the client received."""

    tool: str
    arguments: dict[str, Any]
    data: dict[str, Any] | None
    """structuredContent of a successful call."""
    error: str | None
    """content[0].text of a failed call, the only part of an error the model sees."""
    text: str
    """Every text block, joined: what a client that ignores structuredContent reads."""

    @property
    def label(self) -> str:
        return f"{self.tool}({self.arguments})"


def listing() -> dict[str, Tool]:
    """The product server's tools/list."""
    tools, _ = run_session([])
    return tools


def run_session(calls: Sequence[tuple[str, dict[str, Any]]]) -> tuple[dict[str, Tool], list[Outcome]]:
    """List the tools, then make `calls` in order in one client session."""

    async def session() -> tuple[dict[str, Tool], list[Outcome]]:
        async with Client(create_server(), mode="legacy") as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            outcomes = []
            for name, arguments in calls:
                result = await client.call_tool(name, arguments)
                assert isinstance(result, CallToolResult), result
                text = "\n".join(getattr(block, "text", "") for block in result.content)
                if result.is_error:
                    outcomes.append(Outcome(name, arguments, None, text, text))
                else:
                    assert result.structured_content is not None, f"{name} returned no structuredContent"
                    outcomes.append(Outcome(name, arguments, result.structured_content, None, text))
            return tools, outcomes

    return anyio.run(session)
