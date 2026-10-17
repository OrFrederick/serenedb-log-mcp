"""Build the MCP server: name, instructions and the auto-discovered tools."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from serenedb_log_mcp import __version__
from serenedb_log_mcp.tools import register_tools

SERVER_NAME = "serenedb-log"

# Placeholder until T7 adds instructions.py (at most 4,096 characters).
INSTRUCTIONS = (
    "Read-only log search and aggregation over the Base Corpus (BGL and OpenStack) in SereneDB. "
    "Retrieval returns sample Log Entries; aggregation answers how often. "
    "Matching Log Entries are not Incidents: never count lines as incidents."
)


def create_server() -> MCPServer:
    """Return a new server with every tool module in `serenedb_log_mcp.tools` registered."""
    server: MCPServer = MCPServer(name=SERVER_NAME, version=__version__, instructions=INSTRUCTIONS)
    register_tools(server)
    return server
