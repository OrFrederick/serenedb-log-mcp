"""Stub MCP server (FastMCP) over the dev-loaded SereneDB tables `bgl` and `openstack`.

Throwaway research prototype for issue #3 (eval harness). NOT the product tool set.
Every tool call returns Evidence (evidence_id, executed SQL, params, Data Scope,
total count, returned count, truncated flag, query_ms) and appends it to the
Evidence Ledger JSONL file at $LEDGER_PATH.

SQL is deliberately simple (ILIKE, date_trunc). All user values are bound as
psycopg parameters; identifiers (dataset, group_by, bucket) come from whitelists.
"""
from __future__ import annotations

import json
import os
import time
from typing import Literal, Optional

import psycopg
import psycopg.sql
try:  # mcp 2.x renamed FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

DSN = os.environ.get("DATABASE_URL", "host=localhost port=7890 dbname=postgres user=postgres")
LEDGER = os.environ.get("LEDGER_PATH", "/tmp/evidence_ledger.jsonl")
MAX_ROWS = 50

TABLES = {"BGL": "bgl", "OpenStack": "openstack"}
GROUP_COLS = {
    "BGL": {"component", "level", "node", "alert_tag"},
    "OpenStack": {"component", "level", "source_file", "instance_id"},
}
COLS = {
    "BGL": "line_no, ts, node, component, level, alert_tag, message",
    "OpenStack": "line_no, ts, source_file, component, level, request_id, instance_id, message",
}

mcp = FastMCP("serenedb-log-stub")
_conn: psycopg.Connection | None = None


def conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(DSN, autocommit=True)
    return _conn


def _next_eid() -> str:
    try:
        with open(LEDGER) as f:
            n = sum(1 for _ in f)
    except FileNotFoundError:
        n = 0
    return f"E{n + 1}"


def _jsonable(v):
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ")
    return v


def _run(sql: str, params: list) -> tuple[list[dict], float]:
    t = time.perf_counter()
    cur = conn().execute(sql, params)
    names = [d.name for d in cur.description]
    rows = [{k: _jsonable(v) for k, v in zip(names, r)} for r in cur.fetchall()]
    return rows, (time.perf_counter() - t) * 1000


def _render(sql: str, params: list) -> str:
    """SQL with params inlined, for display/replay (psycopg client-side quoting)."""
    with conn().cursor() as cur:
        return psycopg.sql.SQL(sql.replace("%s", "{}")).format(
            *[psycopg.sql.Literal(p) for p in params]).as_string(cur)


def _evidence(tool: str, args: dict, scope: dict, sql: str, params: list,
              rows: list[dict], total: int, query_ms: float) -> dict:
    ev = {
        "evidence_id": _next_eid(),
        "tool": tool,
        "params": args,
        "data_scope": scope,
        "sql": _render(sql, params),
        "total_count": total,
        "returned_count": len(rows),
        "truncated": len(rows) < total,
        "query_ms": round(query_ms, 1),
        "rows": rows,
    }
    return ev


def _commit(ev: dict) -> dict:
    """Append the FINAL Evidence (after tool-specific extras) to the ledger."""
    with open(LEDGER, "a") as f:
        f.write(json.dumps(ev, default=str) + "\n")
    return ev


def _guard(fn):
    """Surface the real exception text to the agent (MCPServer otherwise says only 'Error executing tool')."""
    import functools, traceback

    @functools.wraps(fn)
    def w(*a, **k):
        try:
            return _commit(fn(*a, **k))
        except Exception as e:  # noqa: BLE001
            with open(LEDGER + ".errors", "a") as f:
                f.write(json.dumps({"tool": fn.__name__, "args": k, "error": repr(e),
                                    "tb": traceback.format_exc()[-800:]}) + "\n")
            global _conn
            _conn = None  # force reconnect next call
            raise ValueError(f"{fn.__name__} failed: {e!r}") from e
    return w


def _where(dataset: str, query: Optional[str], start: Optional[str], end: Optional[str],
           component: Optional[str], level: Optional[str]) -> tuple[str, list, dict]:
    clauses, params = [], []
    if query:
        clauses.append("message ILIKE %s"); params.append(f"%{query}%")
    if start:
        clauses.append("ts >= CAST(%s AS TIMESTAMP)"); params.append(start)
    if end:
        clauses.append("ts < CAST(%s AS TIMESTAMP)"); params.append(end)
    if component:
        clauses.append("component = %s"); params.append(component)
    if level:
        clauses.append("level = %s"); params.append(level)
    scope = {"dataset": dataset, "query": query, "start": start, "end": end,
             "component": component, "level": level}
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params, scope


Dataset = Literal["BGL", "OpenStack"]


@mcp.tool()
@_guard
def describe_corpus() -> dict:
    """Describe the Base Corpus. Purpose: learn which Datasets exist, their row counts,
    time ranges, and the columns usable as filters/group-by, BEFORE choosing other tools.
    Inputs: none. Outputs: Evidence with one row per Dataset (dataset, log_entries,
    first_ts, last_ts) plus the allowed group_by columns."""
    sql = ("SELECT 'BGL' AS dataset, count(*) AS log_entries, min(ts) AS first_ts, max(ts) AS last_ts FROM bgl "
           "UNION ALL SELECT 'OpenStack', count(*), min(ts), max(ts) FROM openstack")
    rows, ms = _run(sql, [])
    ev = _evidence("describe_corpus", {}, {"dataset": "Base Corpus"}, sql, [], rows, len(rows), ms)
    ev["group_by_columns"] = {k: sorted(v) for k, v in GROUP_COLS.items()}
    return ev


@mcp.tool()
@_guard
def search_logs(dataset: Dataset, query: str, start: Optional[str] = None, end: Optional[str] = None,
                component: Optional[str] = None, level: Optional[str] = None, limit: int = 20) -> dict:
    """Full-text search for Log Entries in ONE Dataset (case-insensitive substring match on the message).
    Purpose: find example error lines (Engineer questions). Returns a LIMITED sample, never use the
    sample size as a frequency; use total_count, or count_by/trend for frequencies.
    Inputs: dataset ('BGL'|'OpenStack'), query (substring), optional start/end ('YYYY-MM-DD[ HH:MM:SS]',
    end exclusive), component, level, limit (1-50, default 20).
    Outputs: Evidence {evidence_id, sql, data_scope, total_count, returned_count, truncated, rows[line_no, ts, ...]}.
    Cite results as [<evidence_id>]."""
    limit = max(1, min(int(limit), MAX_ROWS))
    t = TABLES[dataset]
    where, params, scope = _where(dataset, query, start, end, component, level)
    total_rows, ms1 = _run(f"SELECT count(*) AS n FROM {t}{where}", params)
    sql = f"SELECT {COLS[dataset]} FROM {t}{where} ORDER BY ts, line_no LIMIT {limit}"
    rows, ms2 = _run(sql, params)
    return _evidence("search_logs", dict(dataset=dataset, query=query, start=start, end=end,
                     component=component, level=level, limit=limit), scope, sql, params,
                     rows, total_rows[0]["n"], ms1 + ms2)


@mcp.tool()
@_guard
def get_context(dataset: Dataset, line_no: int, before: int = 5, after: int = 5) -> dict:
    """Nearby events around one Log Entry. Purpose: see what happened just before and just after a
    failure (Engineer questions). Use a line_no returned by search_logs.
    Inputs: dataset, line_no, before (0-25, default 5), after (0-25, default 5).
    Outputs: Evidence with the surrounding Log Entries in file order (rows include line_no, ts, component, message)."""
    before, after = max(0, min(before, 25)), max(0, min(after, 25))
    t = TABLES[dataset]
    sql = f"SELECT {COLS[dataset]} FROM {t} WHERE line_no BETWEEN %s AND %s ORDER BY line_no"
    params = [line_no - before, line_no + after]
    rows, ms = _run(sql, params)
    return _evidence("get_context", dict(dataset=dataset, line_no=line_no, before=before, after=after),
                     {"dataset": dataset, "line_no_range": params}, sql, params, rows, len(rows), ms)


@mcp.tool()
@_guard
def count_by(dataset: Dataset, group_by: str, query: Optional[str] = None, start: Optional[str] = None,
             end: Optional[str] = None, component: Optional[str] = None, level: Optional[str] = None,
             top: int = 20) -> dict:
    """Count matching Log Entries grouped by one column, computed in SereneDB over the full Data Scope.
    Purpose: frequencies and which Components are involved (Team Lead / Manager questions).
    Counts are Log Entries, NOT incidents.
    Inputs: dataset, group_by (BGL: component|level|node|alert_tag; OpenStack: component|level|source_file|instance_id),
    optional query (substring filter on message), start/end, component, level, top (1-50 groups, default 20).
    Outputs: Evidence {total_count (= all matching Log Entries), rows[{group, n}] sorted desc, truncated
    (true if more groups exist than returned), groups_total}."""
    if group_by not in GROUP_COLS[dataset]:
        raise ValueError(f"group_by must be one of {sorted(GROUP_COLS[dataset])} for {dataset}")
    top = max(1, min(int(top), MAX_ROWS))
    t = TABLES[dataset]
    where, params, scope = _where(dataset, query, start, end, component, level)
    scope["group_by"] = group_by
    tot, ms1 = _run(f"SELECT count(*) AS n, count(DISTINCT {group_by}) AS g FROM {t}{where}", params)
    sql = f"SELECT {group_by} AS grp, count(*) AS n FROM {t}{where} GROUP BY 1 ORDER BY 2 DESC LIMIT {top}"
    rows, ms2 = _run(sql, params)
    ev = _evidence("count_by", dict(dataset=dataset, group_by=group_by, query=query, start=start, end=end,
                   component=component, level=level, top=top), scope, sql, params, rows,
                   tot[0]["n"], ms1 + ms2)
    ev["groups_total"] = tot[0]["g"]
    ev["truncated"] = len(rows) < tot[0]["g"]
    return ev


@mcp.tool()
@_guard
def trend(dataset: Dataset, bucket: Literal["hour", "day", "week", "month"] = "day",
          query: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
          component: Optional[str] = None, level: Optional[str] = None) -> dict:
    """Trend over time: count of matching Log Entries per time bucket, computed in SereneDB.
    Purpose: is a problem growing, recurring or a one-off spike (Team Lead / Manager questions).
    Inputs: dataset, bucket (hour|day|week|month), optional query, start/end, component, level.
    Outputs: Evidence {total_count (sum over buckets), rows[{bucket, n}] in time order (max 400 buckets; truncated if more)}."""
    t = TABLES[dataset]
    where, params, scope = _where(dataset, query, start, end, component, level)
    scope["bucket"] = bucket
    sql = f"SELECT date_trunc('{bucket}', ts) AS bucket, count(*) AS n FROM {t}{where} GROUP BY 1 ORDER BY 1"
    rows, ms = _run(sql, params)
    total = sum(r["n"] for r in rows)
    ev = _evidence("trend", dict(dataset=dataset, bucket=bucket, query=query, start=start, end=end,
                   component=component, level=level), scope, sql, params, rows[:400], total, ms)
    ev["truncated"] = len(rows) > 400
    ev["buckets_total"] = len(rows)
    return ev


if __name__ == "__main__":
    mcp.run()
