"""A fake SereneDB and a sample Evidence tool for the ledger replay tests (T13).

The sample tool runs its one statement through the real database layer (`db.fetch_all`), and
`FakeConnection` answers `SELECT k, n FROM t WHERE k = %s AND at >= %s` from `DATA`, so a test
changes the "database" by changing `DATA`. `SEEN` records every statement with the parameters it was
bound with, which shows that the replay binds them exactly as the Evidence Ledger stored them (JSON:
a datetime comes back as its ISO string). The key "boom" makes the statement fail.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer

from serenedb_log_mcp import db, ledger
from serenedb_log_mcp.evidence import DataScope, RoleParam
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool

SQL = "SELECT k, n FROM t WHERE k = %s AND at >= %s"
SINCE = datetime(2005, 6, 1, 12, 0)
DATA: dict[str, int] = {}
SEEN: list[tuple[str, Any]] = []


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakeConnection:
    closed = False
    broken = False

    def execute(self, query: str, params: Sequence[Any] | None = None) -> FakeCursor:
        SEEN.append((query, params))
        if query == SQL:
            assert params is not None
            key = params[0]
            if key == "boom":
                raise db.psycopg.errors.InvalidParameterValue("at bound type VARCHAR is incompatible")
            return FakeCursor([(key, DATA[key])] if key in DATA else [])
        return FakeCursor([])

    def cancel_safe(self, *, timeout: float = 30.0) -> None:
        pass

    def add_notice_handler(self, callback: Callable[[Any], None]) -> None:
        pass

    def remove_notice_handler(self, callback: Callable[[Any], None]) -> None:
        pass

    def close(self) -> None:
        pass


def fake_connect(dsn: str, *, client_binding: bool) -> FakeConnection:
    return FakeConnection()


def register_sample(server: MCPServer) -> None:
    @evidence_tool(server, title="Sample: count one key")
    def sample_total(role: RoleParam, key: str, sort: str = "time") -> ToolOutcome:
        """Count the Log Entries of one key since a fixed time.

        Inputs: role, key, sort. Outputs: the Evidence envelope with columns k and n.
        """
        result = db.fetch_all(SQL, [key, SINCE], role=role)
        total = int(result.rows[0][1]) if result.rows else 0
        return ToolOutcome(
            result=result,
            scope=DataScope(datasets=["bgl"], time_from=SINCE.isoformat()),
            columns=["k", "n"],
            summary={"entries_total": total, "share": total / 7},
            total_matches=len(result.rows),
        )


def sample_server() -> MCPServer:
    server = create_server()
    register_sample(server)
    return server


@contextmanager
def fake_database() -> Iterator[None]:
    """DATA reset to two keys, SEEN emptied, and every layer connected to the fake."""
    DATA.clear()
    DATA.update({"KERNDTLB": 152_734, "KERNSTOR": 63_491})
    SEEN.clear()
    previous = db.set_connector(fake_connect)
    try:
        yield
    finally:
        db.set_connector(previous)


def record(**arguments: Any) -> list[dict[str, Any]]:
    """Call the sample tool on the in-memory ledger and return the ledger's records."""
    server = sample_server()
    for args in arguments.get("calls", [{"key": "KERNDTLB"}]):
        result = anyio.run(lambda a=args: server.call_tool("sample_total", {"role": "engineer", **a}))
        assert not result.is_error, result.content
    return [copy.deepcopy(r) for r in ledger.get_ledger().all()]


GOOD = """\
## Observed Facts
- KERNDTLB has 152,734 Log Entries [E1].
## Hypotheses
- One node.
## Unknowns
- Why.
## Next Step
- Check the node.
"""
