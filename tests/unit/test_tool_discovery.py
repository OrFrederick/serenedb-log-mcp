"""Every module in serenedb_log_mcp/tools/ that exposes register(server) is registered automatically."""

import sys
import textwrap
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver import MCPServer

import serenedb_log_mcp.tools as tools_pkg
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools import register_tools

ToolModuleWriter = Callable[[str, str], str]


@pytest.fixture
def tool_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToolModuleWriter]:
    """Write temporary modules into an extra directory of the real tools package."""
    monkeypatch.setattr(tools_pkg, "__path__", [*tools_pkg.__path__, str(tmp_path)])
    written: list[str] = []

    def write(stem: str, source: str) -> str:
        name = f"{stem}_{uuid.uuid4().hex[:8]}"
        (tmp_path / f"{name}.py").write_text(textwrap.dedent(source))
        written.append(name)
        return name

    yield write
    for name in written:
        sys.modules.pop(f"{tools_pkg.__name__}.{name}", None)


def tool_names(server: MCPServer) -> set[str]:
    return {tool.name for tool in anyio.run(server.list_tools)}


def test_module_with_register_is_registered_by_create_server(tool_module: ToolModuleWriter) -> None:
    module = tool_module(
        "tmp_echo",
        """
        def register(server):
            @server.tool(name="tmp_echo", description="Echo the text back.")
            def tmp_echo(text: str) -> str:
                return text
        """,
    )

    server = create_server()

    assert "tmp_echo" in tool_names(server)
    assert module in register_tools(MCPServer(name="other"))


def test_module_without_register_is_skipped(tool_module: ToolModuleWriter) -> None:
    module = tool_module("tmp_helper", "VALUE = 1\n")

    registered = register_tools(MCPServer(name="t"))

    assert module not in registered


def test_private_modules_are_not_imported(tool_module: ToolModuleWriter) -> None:
    tool_module("_tmp_private", "raise RuntimeError('private modules must not be imported')\n")

    register_tools(MCPServer(name="t"))


def test_register_tools_returns_module_names_in_sorted_order(tool_module: ToolModuleWriter) -> None:
    source = "def register(server):\n    pass\n"
    b = tool_module("tmp_b", source)
    a = tool_module("tmp_a", source)

    registered = register_tools(MCPServer(name="t"))

    assert registered.index(a) < registered.index(b)
