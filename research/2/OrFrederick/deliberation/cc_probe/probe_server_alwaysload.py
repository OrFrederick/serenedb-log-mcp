"""Probe: does _meta["anthropic/alwaysLoad"]=True make Claude Code load a tool upfront (no ToolSearch)?"""
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
mcp = MCPServer("probe2", instructions="Probe server 2.")
RO = ToolAnnotations(read_only_hint=True, open_world_hint=False)

@mcp.tool(annotations=RO, meta={"anthropic/alwaysLoad": True}, structured_output=False)
def loaded_upfront() -> str:
    """Returns UPFRONT-MARKER."""
    return "UPFRONT-MARKER"

@mcp.tool(annotations=RO, structured_output=False)
def deferred_tool() -> str:
    """Returns DEFERRED-MARKER."""
    return "DEFERRED-MARKER"

if __name__ == "__main__":
    mcp.run()
