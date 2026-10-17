"""Every module in serenedb_log_mcp/tools/ that exposes register(server) is registered automatically."""

import sys
import textwrap
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import anyio
import pytest
from mcp.server.mcpserver import MCPServer

import serenedb_log_mcp.tools as tools_pkg
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools import ToolModuleError, discover_tool_modules, register_tools

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


def test_public_module_without_register_is_an_error(tool_module: ToolModuleWriter) -> None:
    module = tool_module("tmp_helper", "VALUE = 1\n")

    with pytest.raises(ToolModuleError, match=rf"{module}.*register\(server\).*_{module}"):
        register_tools(MCPServer(name="t"))


def test_module_with_a_non_callable_register_is_an_error(tool_module: ToolModuleWriter) -> None:
    tool_module("tmp_attr", "register = 1\n")

    with pytest.raises(ToolModuleError, match="register"):
        register_tools(MCPServer(name="t"))


def test_module_that_fails_to_import_makes_create_server_raise(tool_module: ToolModuleWriter) -> None:
    tool_module("tmp_broken", "raise RuntimeError('broken tool module')\n")

    with pytest.raises(RuntimeError, match="broken tool module"):
        create_server()


def test_private_modules_are_not_imported(tool_module: ToolModuleWriter) -> None:
    tool_module("_tmp_private", "raise RuntimeError('private modules must not be imported')\n")

    register_tools(MCPServer(name="t"))


def test_register_tools_returns_module_names_in_sorted_order(tool_module: ToolModuleWriter) -> None:
    source = "def register(server):\n    pass\n"
    b = tool_module("tmp_b", source)
    a = tool_module("tmp_a", source)

    registered = register_tools(MCPServer(name="t"))

    assert registered.index(a) < registered.index(b)


def tool_names_per_module(modules: list[ModuleType]) -> dict[str, set[str]]:
    """Register each module on its own fresh server, so that overlapping names stay visible."""
    names: dict[str, set[str]] = {}
    for module in modules:
        server = MCPServer(name="t", warn_on_duplicate_tools=False)
        module.register(server)
        names[module.__name__] = tool_names(server)
    return names


def repeated_tool_names(names: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for tools in names.values():
        repeated |= seen & tools
        seen |= tools
    return repeated


def test_no_two_tool_modules_register_the_same_tool_name() -> None:
    names = tool_names_per_module(discover_tool_modules())

    assert repeated_tool_names(names) == set()


def test_repeated_tool_names_are_detected(tool_module: ToolModuleWriter) -> None:
    source = """
    def register(server):
        @server.tool(name="tmp_same")
        def tmp_same() -> str:
            return "x"
    """
    tool_module("tmp_one", source)
    tool_module("tmp_two", source)

    names = tool_names_per_module(discover_tool_modules())

    assert repeated_tool_names(names) == {"tmp_same"}


def test_discover_tool_modules_skips_private_modules(tool_module: ToolModuleWriter) -> None:
    public = tool_module("tmp_pub", "def register(server):\n    pass\n")
    tool_module("_tmp_priv", "VALUE = 1\n")

    discovered = [m.__name__.rsplit(".", 1)[1] for m in discover_tool_modules()]

    assert public in discovered
    assert not any(name.startswith("_") for name in discovered)
