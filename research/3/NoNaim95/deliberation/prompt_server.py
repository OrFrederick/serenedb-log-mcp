"""Probe: does `claude -p "/mcp__p__engineer ARG"` expand an MCP prompt headlessly?"""
from mcp.server.mcpserver import MCPServer
mcp = MCPServer("p")
@mcp.prompt()
def engineer(dataset: str = "bgl") -> str:
    """Engineer role brief."""
    return f"PROMPT-EXPANDED-MARKER dataset={dataset}. Reply with exactly the word ROLE-ENGINEER-OK and the dataset name."
@mcp.tool()
def ping() -> str:
    """Returns pong."""
    return "pong"
if __name__ == "__main__":
    mcp.run("stdio")
