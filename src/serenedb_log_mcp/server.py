"""Build the MCP server: name, instructions, the Role prompts, the auto-discovered tools and the
MCP Apps visuals."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from serenedb_log_mcp import __version__
from serenedb_log_mcp.config import get_settings
from serenedb_log_mcp.instructions import server_instructions
from serenedb_log_mcp.roles.prompts import register_prompts
from serenedb_log_mcp.tools import register_tools
from serenedb_log_mcp.visuals import apps

SERVER_NAME = "serenedb-log"


def create_server() -> MCPServer:
    """Return a new server with the instructions, the Role prompts and every tool module registered.

    A server pinned to one Role (`--role`, read from the settings the CLI set) says so in its
    instructions and offers only that Role's prompt; its tools reject every other Role (toolkit).
    """
    pin = get_settings().role
    server: MCPServer = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=server_instructions(pin),
        # MCP Apps (T23): serves the ui:// visuals that tools link through _meta.ui.resourceUri.
        extensions=[apps()],
    )
    register_prompts(server, only=pin)
    register_tools(server)
    return server
