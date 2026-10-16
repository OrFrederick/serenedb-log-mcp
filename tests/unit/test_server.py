"""create_server() builds the MCPServer that the CLI runs."""

import anyio
from mcp import Client

from serenedb_log_mcp import __version__
from serenedb_log_mcp.server import SERVER_NAME, create_server


def test_server_is_named_serenedb_log() -> None:
    # Claude Code shows the tools as mcp__serenedb-log__<tool> (plan section 2.3).
    assert SERVER_NAME == "serenedb-log"
    assert create_server().name == "serenedb-log"


def test_in_process_client_sees_name_version_and_instructions() -> None:
    async def handshake() -> tuple[str, str, str | None]:
        async with Client(create_server(), mode="legacy") as client:
            info = client.server_info
            assert info is not None
            return info.name, info.version, client.instructions

    name, version, instructions = anyio.run(handshake)

    assert name == "serenedb-log"
    assert version == __version__
    assert instructions
    assert "Log Entries" in instructions
