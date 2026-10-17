"""The project .mcp.json lets Claude Code start this server next to the SereneDB docs server."""

import json
from pathlib import Path

MCP_JSON = Path(__file__).parents[2] / ".mcp.json"


def test_mcp_json_registers_serenedb_log_over_stdio_next_to_the_docs_server() -> None:
    servers = json.loads(MCP_JSON.read_text())["mcpServers"]

    assert servers["serenedb-log"] == {"type": "stdio", "command": "uv", "args": ["run", "serenedb-log-mcp"]}
    assert servers["serenedb-docs"]["url"] == "https://api.serenedb.com/mcp"
