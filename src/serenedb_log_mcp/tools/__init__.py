"""MCP tools, discovered automatically.

Add a tool by adding a module to this package that exposes `register(server: MCPServer) -> None`.
Nothing else needs editing: `register_tools` imports every public module here (names that do not
start with `_`) in name order and calls its `register`.

Rules, so that a broken tool never disappears silently:
- A public module without a callable `register` is an error. Name helper modules `_<name>.py`.
- An import error in a tool module stops server startup.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from mcp.server.mcpserver import MCPServer


class ToolModuleError(RuntimeError):
    """A public module in the tools package does not follow the tool-module contract."""


def discover_tool_modules() -> list[ModuleType]:
    """Import every public module of this package in name order and check it exposes `register`."""
    modules: list[ModuleType] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if not callable(getattr(module, "register", None)):
            raise ToolModuleError(
                f"{module.__name__} has no callable register(server). Every public module in "
                f"{__name__} is a tool module; rename a helper module to _{info.name}.py."
            )
        modules.append(module)
    return modules


def register_tools(server: MCPServer) -> list[str]:
    """Register every tool module of this package on `server`; return the module names in order."""
    names: list[str] = []
    for module in discover_tool_modules():
        module.register(server)
        names.append(module.__name__.rsplit(".", 1)[1])
    return names
