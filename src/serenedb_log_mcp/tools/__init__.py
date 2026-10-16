"""MCP tools, discovered automatically.

Add a tool by adding a module to this package that exposes `register(server: MCPServer) -> None`.
Nothing else needs editing: `register_tools` imports every public module here (names that do not
start with `_`) in name order and calls its `register`. Modules without `register` are helpers and
are skipped. An import error in a tool module stops server startup on purpose, so a broken tool
never disappears silently.
"""

from __future__ import annotations

import importlib
import pkgutil

from mcp.server.mcpserver import MCPServer


def register_tools(server: MCPServer) -> list[str]:
    """Register every tool module of this package on `server`; return the registered module names."""
    registered: list[str] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        register = getattr(module, "register", None)
        if callable(register):
            register(server)
            registered.append(info.name)
    return registered
