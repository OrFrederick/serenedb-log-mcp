"""Helpers for the MCP Apps tests (T23): an Apps-capable in-process client, the served Incident
timeline, and its pure `timelineModel` run in node on an Evidence envelope."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.client.extension import advertise
from mcp.server.apps import APP_MIME_TYPE, EXTENSION_ID
from mcp_types import TextResourceContents

from serenedb_log_mcp import visuals
from serenedb_log_mcp.server import create_server

APPS_CLIENT = advertise(EXTENSION_ID, {"mimeTypes": [APP_MIME_TYPE]})
"""What an MCP Apps host such as Claude Desktop advertises in `initialize`."""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def read_view(uri: str = visuals.INCIDENT_TIMELINE_URI) -> TextResourceContents:
    """`resources/read` of `uri` through an Apps-capable client."""

    async def read() -> TextResourceContents:
        async with Client(create_server(), mode="legacy", extensions=[APPS_CLIENT]) as client:
            contents = (await client.read_resource(uri)).contents
            assert len(contents) == 1
            content = contents[0]
            assert isinstance(content, TextResourceContents)
            return content

    return anyio.run(read)


def view_script(html: str) -> str:
    """The one inline script of a view."""
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)
    assert len(scripts) == 1, "the timeline carries exactly one inline script"
    return scripts[0]


def view_model(envelope: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    """Run the served timeline's `timelineModel` in node on `envelope`; return what it computed."""
    script = tmp_path / "model.js"
    script.write_text(
        view_script(read_view().text)
        + f"\nprocess.stdout.write(JSON.stringify(timelineModel({json.dumps(envelope)})));\n",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    model: dict[str, Any] = json.loads(result.stdout)
    return model
