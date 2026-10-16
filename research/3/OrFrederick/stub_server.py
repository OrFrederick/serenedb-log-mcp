"""Stub MCP server for issue #3: 3 fake log tools over the real SereneDB `bgl`/`openstack` tables.

Not the product. It mimics the Evidence Ledger contract (evidence id, SQL, params, Data Scope,
total count, returned count, truncated, query time) so the eval harness can be prototyped.
Ledger is appended as JSONL to $LEDGER_PATH so the harness can resolve [E<n>] citations.
"""
import json
import os
import time

import psycopg
from mcp.server.mcpserver import MCPServer  # mcp 2.x: FastMCP was renamed to MCPServer

DSN = os.environ.get("DATABASE_URL", "host=localhost port=7890 dbname=postgres user=postgres password=postgres")
LEDGER = os.environ.get("LEDGER_PATH", "ledger.jsonl")
TABLES = {"bgl": "bgl", "openstack": "openstack"}
mcp = MCPServer("stub-logs")
_conn = None


def _db():
    global _conn
    if _conn is None:
        _conn = psycopg.connect(DSN, autocommit=True)
    return _conn


def _next_id() -> int:
    try:
        with open(LEDGER) as f:
            return sum(1 for _ in f) + 1
    except FileNotFoundError:
        return 1


def _record(tool, sql, params, scope, total, rows, truncated, t0):
    ev = {
        "evidence_id": f"E{_next_id()}", "tool": tool, "sql": sql, "params": params,
        "data_scope": scope, "total_count": total, "returned_count": len(rows),
        "truncated": truncated, "query_ms": round((time.time() - t0) * 1000, 1), "rows": rows,
    }
    with open(LEDGER, "a") as f:
        f.write(json.dumps(ev, default=str) + "\n")
    return ev


@mcp.tool()
def count_by(dataset: str, group_by: str, level: str | None = None, alert_only: bool = False, limit: int = 20) -> dict:
    """Count Log Entries grouped by one column, over a whole Dataset.

    Purpose: aggregation for Team Lead / Manager questions (how often, which Component).
    Inputs: dataset ('bgl' or 'openstack'); group_by ('component', 'level', and for bgl also 'alert_label', 'rack');
    level: optional exact level filter (e.g. 'FATAL', 'ERROR'); alert_only: bgl only, keep alert lines;
    limit: max groups returned (default 20).
    Output: evidence_id (cite as [E<n>]), sql, data_scope, total_count (all matching Log Entries),
    groups [[value, count], ...] sorted by count desc, truncated flag.
    """
    t0 = time.time()
    allowed = {"component", "level", "alert_label", "rack"} if dataset == "bgl" else {"component", "level"}
    if dataset not in TABLES or group_by not in allowed:
        return {"error": f"invalid dataset/group_by; allowed group_by for {dataset}: {sorted(allowed)}"}
    where, params = [], []
    if level:
        where.append("level = %s"); params.append(level)
    if alert_only and dataset == "bgl":
        where.append("alert_label <> '-'")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT {group_by}, count(*) AS n FROM {TABLES[dataset]}{w} GROUP BY 1 ORDER BY 2 DESC"
    rows = _db().execute(sql, params).fetchall()
    total = sum(r[1] for r in rows)
    ev = _record("count_by", sql, params, {"dataset": dataset, "filters": where or "none", "time_range": "full"},
                 total, [list(r) for r in rows[:limit]], len(rows) > limit, t0)
    return {k: ev[k] for k in ("evidence_id", "sql", "data_scope", "total_count", "truncated", "query_ms")} | {"groups": ev["rows"]}


@mcp.tool()
def search_logs(dataset: str, text: str, start: str | None = None, end: str | None = None, limit: int = 10) -> dict:
    """Find Log Entries whose message contains a phrase, optionally inside a time window.

    Purpose: retrieval for Engineer questions (show me the failing lines).
    Inputs: dataset ('bgl' or 'openstack'); text: case-insensitive substring of the message;
    start/end: optional ISO timestamps; limit: max rows (default 10, max 50).
    Output: evidence_id, sql, data_scope, total_count (all matches, not just returned), rows
    [line_no, ts, component/node, content], truncated flag (true when total_count > returned rows).
    """
    t0 = time.time()
    if dataset not in TABLES:
        return {"error": "dataset must be 'bgl' or 'openstack'"}
    limit = min(limit, 50)
    where, params = ["content ILIKE %s"], [f"%{text}%"]
    if start:
        where.append("ts >= %s"); params.append(start)
    if end:
        where.append("ts < %s"); params.append(end)
    w = " WHERE " + " AND ".join(where)
    who = "node" if dataset == "bgl" else "component"
    total = _db().execute(f"SELECT count(*) FROM {TABLES[dataset]}{w}", params).fetchone()[0]
    sql = f"SELECT line_no, ts, {who}, content FROM {TABLES[dataset]}{w} ORDER BY ts LIMIT {limit}"
    rows = [list(r) for r in _db().execute(sql, params).fetchall()]
    ev = _record("search_logs", sql, params, {"dataset": dataset, "start": start, "end": end}, total, rows, total > len(rows), t0)
    return {k: ev[k] for k in ("evidence_id", "sql", "data_scope", "total_count", "returned_count", "truncated", "query_ms", "rows")}


@mcp.tool()
def trend(dataset: str, bucket: str = "day", alert_label: str | None = None, level: str | None = None) -> dict:
    """Count Log Entries per time bucket (a trend over time).

    Purpose: Team Lead / Manager questions about recurrence and spikes.
    Inputs: dataset ('bgl' or 'openstack'); bucket ('hour', 'day', 'month');
    alert_label: bgl only, exact alert category (e.g. 'KERNDTLB'); level: optional exact level.
    Output: evidence_id, sql, data_scope, total_count, series [[bucket_start, count], ...] in time order.
    """
    t0 = time.time()
    if dataset not in TABLES or bucket not in {"hour", "day", "month"}:
        return {"error": "invalid dataset or bucket"}
    where, params = [], []
    if alert_label and dataset == "bgl":
        where.append("alert_label = %s"); params.append(alert_label)
    if level:
        where.append("level = %s"); params.append(level)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT date_trunc('{bucket}', ts) AS b, count(*) FROM {TABLES[dataset]}{w} GROUP BY 1 ORDER BY 1"
    rows = [[str(r[0]), r[1]] for r in _db().execute(sql, params).fetchall()]
    ev = _record("trend", sql, params, {"dataset": dataset, "bucket": bucket, "filters": where or "none"},
                 sum(r[1] for r in rows), rows[:200], len(rows) > 200, t0)
    return {k: ev[k] for k in ("evidence_id", "sql", "data_scope", "total_count", "truncated", "query_ms")} | {"series": ev["rows"]}


if __name__ == "__main__":
    mcp.run("stdio")
