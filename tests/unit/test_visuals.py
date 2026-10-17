"""MCP Apps visuals (T23): the Incident timeline is listed, served and linked from `list_incidents`.

The host side (Claude Desktop rendering the `ui://` resource in an iframe) cannot run here; these
tests pin the server side of the MCP Apps extension (`io.modelcontextprotocol/ui`, ext-apps spec
2026-01-26) as the `mcp` 2.3.0 SDK exposes it, and run the view's pure drawing model in node. The
tool result itself is checked against SereneDB in tests/db/incidents/test_incident_timeline_db.py.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp_types import Resource, Tool
from timeline_view import APPS_CLIENT, needs_node, read_view, view_model, view_script

from serenedb_log_mcp import visuals
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ALWAYS_LOAD_META


def _listing() -> tuple[dict[str, Tool], dict[str, Resource]]:
    """tools/list and resources/list as an MCP Apps host on a handshake-era connection sees them."""

    async def listed() -> tuple[dict[str, Tool], dict[str, Resource]]:
        async with Client(create_server(), mode="legacy", extensions=[APPS_CLIENT]) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            resources = {str(r.uri): r for r in (await client.list_resources()).resources}
            return tools, resources

    return anyio.run(listed)


def _html() -> str:
    return read_view().text


# ----------------------------------------------------------------- the server side of the extension


def _advertised(mode: str) -> dict[str, Any] | None:
    async def handshake() -> dict[str, Any] | None:
        async with Client(create_server(), mode=mode, extensions=[APPS_CLIENT]) as client:
            return client.server_capabilities.extensions

    return anyio.run(handshake)


def test_the_server_advertises_the_mcp_apps_extension_on_the_2026_protocol() -> None:
    extensions = _advertised("auto")

    assert extensions is not None
    assert EXTENSION_ID in extensions


def test_a_handshake_era_connection_does_not_see_the_extension_capability() -> None:
    # `ServerCapabilities.extensions` is 2026-07-28 vocabulary, and mcp 2.3.0 sieves it out of the
    # initialize result on a handshake-era connection (stdio clients today). The link that hosts
    # render from, the tool's _meta.ui.resourceUri, still arrives there (the tests below run in
    # legacy mode). If this starts failing, the SDK now advertises the extension on legacy wires.
    assert _advertised("legacy") is None


def test_the_incident_timeline_resource_is_listed_as_an_mcp_app() -> None:
    _, resources = _listing()

    resource = resources[visuals.INCIDENT_TIMELINE_URI]
    assert resource.uri.startswith("ui://")
    assert resource.mime_type == APP_MIME_TYPE
    assert resource.description


def test_the_incident_timeline_resource_is_served_as_html() -> None:
    content = read_view(visuals.INCIDENT_TIMELINE_URI)

    assert content.mime_type == APP_MIME_TYPE
    assert content.text.lstrip().lower().startswith("<!doctype html>")
    assert "Incident" in content.text


def test_list_incidents_links_the_incident_timeline() -> None:
    tools, resources = _listing()

    meta = tools["list_incidents"].meta or {}
    assert meta["ui"]["resourceUri"] == visuals.INCIDENT_TIMELINE_URI
    assert meta["ui"]["resourceUri"] in resources


def test_linking_the_timeline_keeps_the_always_load_meta() -> None:
    tools, _ = _listing()

    meta = tools["list_incidents"].meta or {}
    for key, value in ALWAYS_LOAD_META.items():
        assert meta[key] == value


def test_only_the_linked_tools_carry_a_ui_resource() -> None:
    tools, _ = _listing()

    linked = {name for name, tool in tools.items() if "ui" in (tool.meta or {})}
    assert linked == set(visuals.TOOL_RESOURCES) == {"list_incidents"}


def test_every_linked_tool_exists_and_every_linked_resource_is_served() -> None:
    tools, resources = _listing()

    for tool, uri in visuals.TOOL_RESOURCES.items():
        assert tool in tools
        assert uri in resources


def test_a_client_without_mcp_apps_still_lists_the_tool_unchanged() -> None:
    # Graceful degradation (SEP-2133): the link is static metadata that a client without Apps
    # ignores, so the tool, its schemas and its results are the same for every client.
    async def listed() -> Tool:
        async with Client(create_server(), mode="legacy") as client:
            return {t.name: t for t in (await client.list_tools()).tools}["list_incidents"]

    plain = anyio.run(listed)
    tools, _ = _listing()

    apps = tools["list_incidents"]
    assert plain.input_schema == apps.input_schema
    assert plain.output_schema == apps.output_schema
    assert plain.meta == apps.meta


def test_ui_meta_is_none_for_a_tool_without_a_visual() -> None:
    assert visuals.ui_meta("count_by") is None
    assert visuals.ui_meta("list_incidents") == {"ui": {"resourceUri": visuals.INCIDENT_TIMELINE_URI}}


# ----------------------------------------------------------------- what the HTML does


def test_the_timeline_speaks_the_mcp_apps_handshake() -> None:
    html = _html()

    for method in (
        "ui/initialize",
        "ui/notifications/initialized",
        "ui/notifications/tool-result",
        "ui/notifications/size-changed",
        "ui/notifications/host-context-changed",
    ):
        assert method in html, method
    assert "appCapabilities" in html
    assert "appInfo" in html
    assert "protocolVersion" in html


def test_the_timeline_loads_nothing_from_outside() -> None:
    # Without _meta.ui.csp the host applies the restrictive default CSP (connect-src 'none', no
    # external scripts or styles), so everything must be inline.
    html = _html().replace('"http://www.w3.org/2000/svg"', "")  # the SVG namespace, never fetched

    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+href=", html)
    assert "fetch(" not in html
    assert "http://" not in html
    assert "https://" not in html


def test_the_timeline_keeps_log_entries_and_incidents_apart() -> None:
    html = _html()

    # entries_total and incidents_total are different units; the header shows both, labelled.
    assert "entries_total" in html
    assert "incidents_total" in html
    assert "Log Entries" in html
    assert "Incidents" in html
    assert "incidents" not in re.sub(r"incidents_total|list_incidents", "", html)


def test_the_timeline_cites_the_evidence_id() -> None:
    assert "evidence_id" in _html()


def test_the_timeline_reads_the_columns_by_name() -> None:
    # The rows are positional; the view looks columns up by name so a new column cannot shift it.
    script = view_script(_html())

    assert "columns.indexOf" in script
    for column in ("pattern_key", "t0", "t1", "entries"):
        assert f'"{column}"' in script, column


@needs_node
def test_the_timeline_script_parses(tmp_path: Path) -> None:
    script = tmp_path / "timeline.js"
    script.write_text(view_script(_html()), encoding="utf-8")

    result = subprocess.run(
        ["node", "--check", str(script)], capture_output=True, text=True, timeout=30, check=False
    )

    assert result.returncode == 0, result.stderr


BGL_ENVELOPE: dict[str, Any] = {
    "evidence_id": "E7",
    "tool": "list_incidents",
    "total_matches": 3,
    "returned": 2,
    "truncated": True,
    "columns": ["pattern_key", "incident_id", "t0", "t1", "duration_s", "entries", "nodes", "shape"],
    "rows": [
        ["KERNDTLB", 11, "2005-06-11T10:00:00", "2005-06-11T12:00:00", 7200, 152182, 1, "single-node storm"],
        ["KERNMNTF", 12, "2005-06-12T00:00:00", "2005-06-12T00:00:00", 0, 4, 4, "multi-midplane"],
    ],
    "summary": {"entries_total": 152190, "incidents_total": 3},
}


@needs_node
def test_the_model_places_one_bar_per_incident_in_time_order(tmp_path: Path) -> None:
    model = view_model(BGL_ENVELOPE, tmp_path)

    bars = model["bars"]
    assert [bar["pattern_key"] for bar in bars] == ["KERNDTLB", "KERNMNTF"]
    assert bars[0]["start"] == 0
    assert bars[1]["end"] == pytest.approx(1)
    assert bars[0]["end"] < bars[1]["start"]
    assert [bar["entries"] for bar in bars] == [152182, 4]
    assert bars[0]["shape"] == "single-node storm"
    assert model["lanes"] == ["KERNDTLB", "KERNMNTF"]


@needs_node
def test_the_model_quotes_the_summary_totals_and_the_truncation(tmp_path: Path) -> None:
    model = view_model(BGL_ENVELOPE, tmp_path)

    assert model["evidence_id"] == "E7"
    assert model["entries_total"] == 152190
    assert model["incidents_total"] == 3
    assert model["shown"] == 2
    assert model["truncated"] is True


@needs_node
def test_the_model_of_a_result_without_rows_is_empty_not_broken(tmp_path: Path) -> None:
    model = view_model({**BGL_ENVELOPE, "rows": [], "returned": 0, "truncated": False}, tmp_path)

    assert model["bars"] == []
    assert model["incidents_total"] == 3


@needs_node
def test_the_model_of_another_tool_is_refused(tmp_path: Path) -> None:
    model = view_model({**BGL_ENVELOPE, "tool": "count_by"}, tmp_path)

    assert model["error"]
