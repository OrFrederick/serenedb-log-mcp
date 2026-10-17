"""Helpers for the aggregation tests: call a tool through an in-process MCP client, read its rows,
and run independent SQL against the same canonical tables."""

from __future__ import annotations

from typing import Any

import anyio
import psycopg
from mcp import Client
from mcp_types import CallToolResult
from psycopg import sql

from serenedb_log_mcp.config import get_settings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.server import create_server


def _call(tool: str, arguments: dict[str, Any]) -> CallToolResult:
    async def one_call() -> CallToolResult:
        async with Client(create_server(), mode="legacy") as client:
            result = await client.call_tool(tool, arguments)
            assert isinstance(result, CallToolResult)
            return result

    return anyio.run(one_call)


def call(tool: str, **arguments: Any) -> dict[str, Any]:
    """The structuredContent of a successful call; fails the test with the error text otherwise."""
    result = _call(tool, arguments)
    if result.is_error:
        text = result.content[0].text if result.content else "(no text)"
        raise AssertionError(f"{tool} failed: {text}")
    assert result.structured_content is not None
    return result.structured_content


def call_error(tool: str, **arguments: Any) -> str:
    """The error text of a call that must fail."""
    result = _call(tool, arguments)
    assert result.is_error, f"{tool} did not fail: {result.structured_content}"
    return result.content[0].text


def row_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows of an Evidence result as dicts keyed by column name."""
    return [dict(zip(result["columns"], row, strict=True)) for row in result["rows"]]


def independent(schema: str, query: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    """Run `query` with {bgl}, {os}, {bgl_idx}, {os_idx} and {owners} bound to the tables in `schema`."""
    settings = get_settings().model_copy(update={"slm_schema": schema})
    bgl, os_ = relation("bgl", settings=settings), relation("openstack", settings=settings)
    statement = sql.SQL(query).format(
        bgl=bgl.table, os=os_.table, bgl_idx=bgl.index, os_idx=os_.index, owners=bgl.ownership_table
    )
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(statement, params).fetchall()]
