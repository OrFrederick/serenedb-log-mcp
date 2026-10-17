"""The Incident timeline (T23) on a real `list_incidents` result, on the lab corpus.

An MCP Apps host renders the `ui://` resource that the called tool's `_meta.ui.resourceUri` names and
posts the tool result to it unchanged. These tests make the same calls a host makes and check that
the result is the one every other client gets, and that the view's drawing model reads it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest
from incident_corpus import STORM_ENTRIES
from mcp import Client
from mcp.server.apps import APP_MIME_TYPE
from mcp_types import CallToolResult, TextResourceContents
from timeline_view import APPS_CLIENT, needs_node, view_model

from serenedb_log_mcp import visuals
from serenedb_log_mcp.server import create_server

pytestmark = pytest.mark.db

ARGUMENTS: dict[str, Any] = {"role": "engineer", "dataset": "bgl", "pattern_key": "KERNDTLB", "sort": "time"}

# Differ between two calls of the same query: the ledger id and the measured time.
PER_CALL = ("evidence_id", "query_ms")


def _as_host(arguments: dict[str, Any]) -> tuple[str, TextResourceContents, CallToolResult]:
    """What an MCP Apps host does: find the tool's UI resource, read it, call the tool."""

    async def host() -> tuple[str, TextResourceContents, CallToolResult]:
        async with Client(create_server(), mode="legacy", extensions=[APPS_CLIENT]) as client:
            tool = {t.name: t for t in (await client.list_tools()).tools}["list_incidents"]
            uri = (tool.meta or {})["ui"]["resourceUri"]
            content = (await client.read_resource(uri)).contents[0]
            assert isinstance(content, TextResourceContents)
            result = await client.call_tool("list_incidents", arguments)
            assert isinstance(result, CallToolResult)
            return uri, content, result

    return anyio.run(host)


def _as_plain_client(arguments: dict[str, Any]) -> CallToolResult:
    async def plain() -> CallToolResult:
        async with Client(create_server(), mode="legacy") as client:
            result = await client.call_tool("list_incidents", arguments)
            assert isinstance(result, CallToolResult)
            return result

    return anyio.run(plain)


def _stable(structured: dict[str, Any] | None) -> dict[str, Any]:
    assert structured is not None
    return {key: value for key, value in structured.items() if key not in PER_CALL}


def test_the_called_tool_references_the_served_timeline(lab: str) -> None:
    uri, content, result = _as_host(ARGUMENTS)

    assert uri == visuals.INCIDENT_TIMELINE_URI
    assert content.mime_type == APP_MIME_TYPE
    assert "timelineModel" in content.text
    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["tool"] == "list_incidents"


def test_the_result_is_the_same_with_and_without_mcp_apps(lab: str) -> None:
    _, _, with_apps = _as_host(ARGUMENTS)
    without = _as_plain_client(ARGUMENTS)

    assert _stable(with_apps.structured_content) == _stable(without.structured_content)
    assert [c.type for c in with_apps.content] == [c.type for c in without.content] == ["text"]
    assert with_apps.meta == without.meta


@needs_node
def test_the_view_draws_every_returned_incident_of_a_real_result(lab: str, tmp_path: Path) -> None:
    _, _, result = _as_host(ARGUMENTS)
    envelope = result.structured_content
    assert envelope is not None

    model = view_model(envelope, tmp_path)

    assert "error" not in model
    assert len(model["bars"]) == envelope["returned"] == 2
    assert model["entries_total"] == envelope["summary"]["entries_total"]
    assert model["incidents_total"] == envelope["summary"]["incidents_total"]
    assert model["evidence_id"] == envelope["evidence_id"]
    assert STORM_ENTRIES in [bar["entries"] for bar in model["bars"]]
    assert model["lanes"] == ["KERNDTLB"]


@needs_node
def test_the_view_draws_an_openstack_result(lab: str, tmp_path: Path) -> None:
    _, _, result = _as_host({"role": "engineer", "dataset": "openstack", "sort": "time"})
    envelope = result.structured_content
    assert envelope is not None

    model = view_model(envelope, tmp_path)

    assert "error" not in model
    assert len(model["bars"]) == envelope["returned"] > 0
    assert {bar["spread_unit"] for bar in model["bars"]} == {"sources"}
