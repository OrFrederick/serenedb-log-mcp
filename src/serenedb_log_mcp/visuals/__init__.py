"""MCP Apps visuals (T23): `ui://` resources that hosts render next to a tool result.

The MCP Apps extension (`io.modelcontextprotocol/ui`, ext-apps spec 2026-01-26) links a tool to an
HTML resource through the tool's `_meta.ui.resourceUri`. A host that negotiated the extension
(Claude Desktop, claude.ai) reads the resource with `resources/read`, renders it in a sandboxed
iframe and posts the finished tool result to it (`ui/notifications/tool-result`). Every other client
ignores the link and reads the same `structuredContent` and text block as before: the visual changes
neither the tool's schemas nor its result.

Two hooks wire this in without touching the tool modules:
- `apps()` builds the `mcp.server.apps.Apps` extension that serves the resources; `create_server`
  passes it to `MCPServer(extensions=[...])`, which also advertises the extension capability;
- `ui_meta(tool)` is the `_meta` entry `toolkit.role_tool` merges into a tool's registration, from
  the static `TOOL_RESOURCES` map.

One visual so far: the Incident timeline for `list_incidents`. The rack heatmap and the ranked risk
table wait until a human has seen the timeline render in Claude Desktop (#35).
"""

from __future__ import annotations

from functools import cache
from importlib.resources import files
from typing import Any

from mcp.server.apps import Apps

__all__ = ["INCIDENT_TIMELINE_URI", "TOOL_RESOURCES", "apps", "resource_html", "ui_meta"]

INCIDENT_TIMELINE_URI = "ui://serenedb-log/incident-timeline.html"

TOOL_RESOURCES: dict[str, str] = {"list_incidents": INCIDENT_TIMELINE_URI}
"""Which tool's results each `ui://` resource renders. A tool named here must exist (tested)."""

_RESOURCES: dict[str, tuple[str, str, str]] = {
    INCIDENT_TIMELINE_URI: (
        "incident-timeline.html",
        "Incident timeline",
        "The Incidents of one list_incidents result on a time axis, one lane per Pattern, with the "
        "summary's entries_total and incidents_total and the Evidence id.",
    ),
}
"""uri -> (file in this package, title, description)."""


@cache
def resource_html(uri: str) -> str:
    """The HTML document served for `uri`."""
    filename = _RESOURCES[uri][0]
    return files(__package__).joinpath(filename).read_text(encoding="utf-8")


def apps() -> Apps:
    """A new MCP Apps extension serving every visual of this package."""
    extension = Apps()
    for uri, (_, title, description) in _RESOURCES.items():
        # The host draws the frame: the view has no border or card of its own.
        extension.add_html_resource(
            uri,
            resource_html(uri),
            name=uri.rsplit("/", 1)[1],
            title=title,
            description=description,
            prefers_border=True,
        )
    return extension


def ui_meta(tool: str) -> dict[str, Any] | None:
    """The `_meta` entry that links `tool` to its visual, or None for a tool without one."""
    uri = TOOL_RESOURCES.get(tool)
    return None if uri is None else {"ui": {"resourceUri": uri}}
