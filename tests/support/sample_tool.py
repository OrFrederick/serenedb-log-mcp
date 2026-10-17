"""A sample Evidence tool, so the Evidence contract can be tested without a database.

This is test support, not part of the product: the real tools arrive with T5 and T6. `FakeQueryResult`
stands in for T2's `db.QueryResult` (plan section 2.7), which is why the Evidence contract defines
`QueryResultLike` structurally instead of importing the database layer.

Run as a script it starts a stdio MCP server with this tool plus the product tools, which is how the
two-process `LEDGER_PATH` test gets two real servers on one ledger.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp.evidence import DataScope, RoleParam, ScopeFilters
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool

SAMPLE_SQL = "SELECT alert_tag, count(*) AS entries FROM bgl GROUP BY 1 ORDER BY 2 DESC LIMIT %s"
SAMPLE_COLUMNS = ["alert_tag", "entries"]
SAMPLE_ROWS: list[list[Any]] = [["KERNDTLB", 152_734], ["KERNSTOR", 63_491], ["APPSEV", 49_651]]
SAMPLE_ENTRIES_TOTAL = 265_876


@dataclass(frozen=True)
class FakeQueryResult:
    """What T2's `QueryResult` provides: total, rows, truncated, query_ms, sql, params, notes."""

    total: int = len(SAMPLE_ROWS)
    rows: list[list[Any]] = field(default_factory=lambda: [list(row) for row in SAMPLE_ROWS])
    truncated: bool = False
    query_ms: float = 12.34
    sql: list[str] = field(default_factory=lambda: [SAMPLE_SQL])
    params: list[list[Any]] = field(default_factory=lambda: [[25]])
    notes: list[str] = field(default_factory=list)


def sample_outcome(groups: int = len(SAMPLE_ROWS), notes: list[str] | None = None) -> ToolOutcome:
    """The outcome a real aggregation tool would hand to the wrapper."""
    rows = [list(row) for row in SAMPLE_ROWS[:groups]]
    return ToolOutcome(
        result=FakeQueryResult(
            total=len(SAMPLE_ROWS),
            rows=rows,
            truncated=len(rows) < len(SAMPLE_ROWS),
            notes=notes or [],
        ),
        scope=DataScope(
            datasets=["bgl"],
            time_from="2005-06-03T15:42:50",
            time_to="2006-01-04T00:00:00",
            filters=ScopeFilters.of(group_by="alert_tag"),
        ),
        columns=list(SAMPLE_COLUMNS),
        summary={
            "groups_total": len(SAMPLE_ROWS),
            "entries_total": SAMPLE_ENTRIES_TOTAL,
            # Only here, so the two-process test can show the records come from two processes.
            "server_pid": os.getpid(),
        },
    )


def register_sample_tools(server: MCPServer) -> None:
    """Register the sample Evidence tool on `server`."""

    @evidence_tool(server, title="Sample: count Log Entries by alert category")
    def sample_count(role: RoleParam, groups: int = len(SAMPLE_ROWS), fail: bool = False) -> ToolOutcome:
        """Count Log Entries per BGL alert category, from fixed sample numbers.

        Inputs: role, groups (how many groups to return, fewer than three truncates the result),
        fail (raise the empty-result error instead of answering).
        Outputs: the Evidence envelope with columns alert_tag and entries.
        """
        if fail:
            raise ToolError(
                "No Log Entries matched. Widen the time range, drop a Component filter, or check "
                "the spelling with describe_corpus."
            )
        return sample_outcome(groups=groups)


SEARCH_SQL = "SELECT entry_id, ts, node, component, level, message FROM bgl_entry_idx WHERE message @@ %s"
SEARCH_COLUMNS = ["entry_id", "ts", "node", "component", "level", "message"]
SEARCH_ROWS: list[list[Any]] = [
    [100 + i, f"2005-06-11T10:00:0{i}", "R30-M0-N9-C:J16-U01", "KERNEL", "FATAL", "data TLB error interrupt"]
    for i in range(5)
]
SEARCH_TOTAL = 152_182
SEARCH_SUMMARY: dict[str, Any] = {
    "entries_total": SEARCH_TOTAL,
    "distinct_sources": 1,
    "top_components": {"KERNEL": SEARCH_TOTAL},
}


def search_outcome(limit: int = len(SEARCH_ROWS)) -> ToolOutcome:
    """What a retrieval tool hands to the wrapper: raw Log Entries, a sample of an exact total."""
    rows = [list(row) for row in SEARCH_ROWS[:limit]]
    return ToolOutcome(
        result=FakeQueryResult(
            total=SEARCH_TOTAL, rows=rows, truncated=True, sql=[SEARCH_SQL], params=[["TLB error"]]
        ),
        scope=DataScope(datasets=["bgl"], filters=ScopeFilters.of(query="TLB error")),
        columns=list(SEARCH_COLUMNS),
        summary=dict(SEARCH_SUMMARY),
        notes=["The rows are examples; use count_by or trend for how often."],
    )


def register_sample_search(server: MCPServer) -> None:
    """Register a sample retrieval tool: its rows are raw Log Entries (it has a message column)."""

    @evidence_tool(server, title="Sample: search Log Entries")
    def sample_search(role: RoleParam, limit: int = len(SEARCH_ROWS)) -> ToolOutcome:
        """Find example Log Entries for 'TLB error', from fixed sample rows.

        Inputs: role, limit (how many of the five sample rows to return).
        Outputs: the Evidence envelope with columns entry_id, ts, node, component, level and message.
        """
        return search_outcome(limit)


def build_sample_server() -> MCPServer:
    """The product server plus the sample Evidence tool."""
    server = create_server()
    register_sample_tools(server)
    return server


if __name__ == "__main__":
    build_sample_server().run("stdio")
