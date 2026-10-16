"""Throwaway prototype MCP server for issue #2 (mcp 2.x MCPServer, stdio).

Tools: describe_corpus, search_logs, get_context, count_by, trend.
Each returns compact Markdown text (what the model reads) plus structuredContent
validated against a Pydantic output model, both carrying the Evidence fields from
docs/decisions.md. Read-only by construction: whitelisted SQL fragments, all user
values bound through psycopg (ClientCursor, see findings.md for why), row/bucket
limits, client-side query timeout via cancel.

Env: DATABASE_URL (default local), R2P_BGL_IDX / R2P_OS_IDX index names,
SLDB_TIMEOUT_S (default 15).
"""
from __future__ import annotations

import datetime as dt
import itertools
import os
import threading
import time
from typing import Annotated, Any, Literal

import psycopg
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

DSN = os.environ.get("DATABASE_URL", "host=localhost port=7890 dbname=postgres user=postgres")
TIMEOUT_S = float(os.environ.get("SLDB_TIMEOUT_S", "15"))
MSG_CHARS = 240  # per-entry message cap in output

DATASETS = {
    "bgl": dict(table="bgl", idx=os.environ.get("R2P_BGL_IDX", "r2p_bgl_idx"), source="node",
                cols="line_no, ts, node AS source, component, level, alert_tag"),
    "openstack": dict(table="openstack", idx=os.environ.get("R2P_OS_IDX", "r2p_os_idx"), source="source_file",
                      cols="line_no, ts, source_file AS source, component, level, request_id, instance_id"),
}
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
_EVID = itertools.count(1)
LEDGER: dict[str, dict] = {}

mcp = MCPServer(
    name="serenedb-log-mcp-proto",
    instructions=(
        "Log search and aggregation over the Base Corpus (BGL supercomputer logs, OpenStack cloud logs) in SereneDB. "
        "Start with describe_corpus to learn Datasets, time ranges and Components. Use search_logs to find Log Entries, "
        "get_context for what happened just before/after one entry, count_by and trend for how often. "
        "Cite every number with its evidence_id as [E<n>]. Matching Log Entries are not incidents."
    ),
)


# ----------------------------------------------------------------- models
class DataScope(BaseModel):
    datasets: list[str]
    time_from: str | None = None
    time_to: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    evidence_id: str = Field(description="Cite as [E<n>] next to every number taken from this result")
    tool: str
    sql: list[str] = Field(description="Executed SQL statements (parameter placeholders shown as %s)")
    params: list[list[Any]]
    data_scope: DataScope
    total_count: int | None = Field(description="Exact number of matching Log Entries (or groups) in the Data Scope")
    returned_count: int
    truncated: bool = Field(description="True when returned_count < total_count")
    query_ms: float


class LogEntry(BaseModel):
    dataset: str
    line_no: int
    ts: str
    source: str | None = Field(description="BGL node or OpenStack source file")
    component: str | None
    level: str | None
    message: str = Field(description="Message text, highlighted with **term** when from a search, capped at 240 chars")
    score: float | None = None
    request_id: str | None = None
    instance_id: str | None = None
    alert_tag: str | None = None
    is_anchor: bool = False


class SearchResult(BaseModel):
    evidence: Evidence
    entries: list[LogEntry]


class Group(BaseModel):
    key: str | None
    count: int
    share: float = Field(description="count / total_entries")


class CountResult(BaseModel):
    evidence: Evidence
    total_entries: int = Field(description="Matching Log Entries across all groups")
    groups: list[Group]


class Bucket(BaseModel):
    bucket: str
    count: int


class TrendResult(BaseModel):
    evidence: Evidence
    bucket: str
    buckets: list[Bucket]


class DatasetInfo(BaseModel):
    dataset: str
    entries: int
    time_from: str
    time_to: str
    components: dict[str, int]
    levels: dict[str, int]


class CorpusResult(BaseModel):
    evidence: Evidence
    datasets: list[DatasetInfo]


# ----------------------------------------------------------------- db helpers
def _q(sql: str, params: list[Any]) -> tuple[list[tuple], float]:
    """Run one read-only statement with a client-side timeout (server ignores statement_timeout)."""
    t = time.perf_counter()
    try:
        with psycopg.connect(DSN, autocommit=False, cursor_factory=psycopg.ClientCursor) as conn:
            conn.read_only = True
            timer = threading.Timer(TIMEOUT_S, conn.cancel_safe)
            timer.start()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                timer.cancel()
            conn.rollback()
    except psycopg.errors.QueryCanceled as e:
        raise ToolError(f"Query exceeded the {TIMEOUT_S:.0f}s limit and was cancelled. Narrow the time range, "
                        "add a component/level filter, or use a coarser bucket.") from e
    except psycopg.OperationalError as e:
        raise ToolError("SereneDB is unreachable or restarted; retry the same call once.") from e
    except psycopg.Error as e:
        raise ToolError(f"SereneDB rejected the query: {str(e).splitlines()[0][:300]}") from e
    return rows, (time.perf_counter() - t) * 1000


def _ts(s: str | None, name: str) -> dt.datetime | None:
    if s is None:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", ""))
    except ValueError as e:
        raise ToolError(f"{name}={s!r} is not ISO-8601. Use e.g. '2005-07-01' or '2005-07-01T12:00:00'.") from e


def _evidence(tool, sqls, params, scope, total, returned, ms) -> Evidence:
    ev = Evidence(evidence_id=f"E{next(_EVID)}", tool=tool, sql=[" ".join(s.split()) for s in sqls],
                  params=[[str(p) if isinstance(p, (dt.datetime, dt.date)) else p for p in ps] for ps in params],
                  data_scope=scope, total_count=total, returned_count=returned,
                  truncated=(total is not None and returned < total), query_ms=round(ms, 1))
    LEDGER[ev.evidence_id] = ev.model_dump(mode="json")
    return ev


def _ev_md(ev: Evidence) -> str:
    s = ev.data_scope
    flt = ", ".join(f"{k}={v}" for k, v in s.filters.items()) or "none"
    trunc = f" **TRUNCATED: showing {ev.returned_count} of {ev.total_count}**" if ev.truncated else ""
    return (f"[{ev.evidence_id}] scope: {'+'.join(s.datasets)} {s.time_from or 'start'}..{s.time_to or 'end'}; "
            f"filters: {flt}; total={ev.total_count} returned={ev.returned_count}{trunc}; {ev.query_ms} ms\n"
            f"SQL: {ev.sql[0][:400]}\n")


def _result(model: BaseModel, md: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=md)],
                          structured_content=model.model_dump(mode="json"))


def _filters(ds: str, components, levels, sources, t0, t1, on_index: bool) -> tuple[str, list[Any]]:
    """Filter fragment. On the index relation every filter is a TSQUERY with ::score(NULL)
    so it does not perturb BM25 (plain SQL filters add to the score, see findings.md)."""
    src = DATASETS[ds]["source"]
    w, p = [], []
    if on_index:
        if t0 or t1:
            w.append("ts @@ ts_between(%s, %s, true, false)::score(NULL)")
            p += [t0 or dt.datetime(1970, 1, 1), t1 or dt.datetime(2100, 1, 1)]
        for col, vals in (("component", components), ("level", levels), (src, sources)):
            if vals:
                w.append(f"{col} @@ ts_any(%s::VARCHAR[])::score(NULL)")
                p.append(list(vals))
    else:
        if t0:
            w.append("ts >= %s"); p.append(t0)
        if t1:
            w.append("ts < %s"); p.append(t1)
        for col, vals in (("component", components), ("level", levels), (src, sources)):
            if vals:
                w.append(f"{col} IN ({', '.join(['%s'] * len(vals))})"); p += list(vals)
    return "".join(" AND " + x for x in w), p


def _tsquery(mode: str) -> str:
    return {"any_words": "%s", "all_words": "websearch_to_tsquery(%s)", "web": "websearch_to_tsquery(%s)",
            "phrase": "ts_phrase(%s)", "fuzzy": "ts_levenshtein(%s, 1)"}[mode]


Dataset = Literal["bgl", "openstack"]


def _entry(ds, r, score=None, anchor=None) -> LogEntry:
    d = dict(zip(r[0], r[1]))
    return LogEntry(dataset=ds, line_no=d["line_no"], ts=str(d["ts"]), source=d.get("source"), component=d.get("component"),
                    level=d.get("level"), message=(d.get("message") or "")[:MSG_CHARS], score=d.get("score"),
                    request_id=d.get("request_id"), instance_id=d.get("instance_id"), alert_tag=d.get("alert_tag"),
                    is_anchor=(anchor is not None and d["line_no"] == anchor))


# ----------------------------------------------------------------- tools
@mcp.tool(annotations=READ_ONLY)
def describe_corpus() -> Annotated[CallToolResult, CorpusResult]:
    """Describe the Base Corpus: per Dataset (bgl, openstack) the number of Log Entries, first/last timestamp,
    and Log Entry counts per Component and per level. Call this first to learn valid filter values and time ranges.
    No inputs. Output: one row per Dataset plus Evidence."""
    sqls = ["SELECT 'bgl', count(*), min(ts), max(ts) FROM bgl UNION ALL SELECT 'openstack', count(*), min(ts), max(ts) FROM openstack",
            "SELECT 'bgl', 'c', component, count(*) FROM bgl GROUP BY 3 UNION ALL SELECT 'bgl', 'l', level, count(*) FROM bgl GROUP BY 3 "
            "UNION ALL SELECT 'openstack', 'c', component, count(*) FROM openstack GROUP BY 3 "
            "UNION ALL SELECT 'openstack', 'l', level, count(*) FROM openstack GROUP BY 3"]
    a, ms1 = _q(sqls[0], [])
    b, ms2 = _q(sqls[1], [])
    infos = []
    for ds, n, t0, t1 in a:
        comps = {str(k): c for d, kind, k, c in sorted(b, key=lambda x: -x[3]) if d == ds and kind == "c"}
        lv = {str(k): c for d, kind, k, c in sorted(b, key=lambda x: -x[3]) if d == ds and kind == "l"}
        infos.append(DatasetInfo(dataset=ds, entries=n, time_from=str(t0), time_to=str(t1), components=comps, levels=lv))
    ev = _evidence("describe_corpus", sqls, [[], []], DataScope(datasets=["bgl", "openstack"]),
                   len(infos), len(infos), ms1 + ms2)
    md = _ev_md(ev) + "\n".join(
        f"- **{i.dataset}**: {i.entries} entries, {i.time_from} .. {i.time_to}; components: "
        + ", ".join(f"{k}={v}" for k, v in i.components.items()) + "; levels: "
        + ", ".join(f"{k}={v}" for k, v in i.levels.items()) for i in infos)
    return _result(CorpusResult(evidence=ev, datasets=infos), md)


@mcp.tool(annotations=READ_ONLY)
def search_logs(
    query: Annotated[str, Field(description="Words to find in the message, e.g. 'ciod failed'")],
    dataset: Dataset = "bgl",
    mode: Annotated[Literal["all_words", "any_words", "phrase", "fuzzy", "web"], Field(
        description="all_words (default): every word must appear; any_words: at least one; phrase: exact word "
                    "sequence; fuzzy: each word within edit distance 1 (typos); web: websearch syntax with \"quoted phrases\", "
                    "OR and -exclusions")] = "all_words",
    time_from: Annotated[str | None, Field(description="ISO start, inclusive")] = None,
    time_to: Annotated[str | None, Field(description="ISO end, exclusive")] = None,
    components: Annotated[list[str] | None, Field(description="Exact Component names from describe_corpus")] = None,
    levels: Annotated[list[str] | None, Field(description="Exact levels, e.g. ['FATAL','ERROR']")] = None,
    sources: Annotated[list[str] | None, Field(description="BGL node ids or OpenStack source files")] = None,
    order: Literal["relevance", "newest", "oldest"] = "relevance",
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> Annotated[CallToolResult, SearchResult]:
    """Full-text search of Log Entries (BM25 ranking, phrase, fuzzy) with time, Component, level and source filters.
    Returns at most `limit` entries with highlighted messages plus the EXACT total number of matching entries,
    so `truncated` tells you whether you saw everything. Use count_by/trend, not this, to say how often something
    happens. Next: get_context(dataset, line_no) on an interesting entry."""
    ds = DATASETS[dataset]
    t0, t1 = _ts(time_from, "time_from"), _ts(time_to, "time_to")
    f, fp = _filters(dataset, components, levels, sources, t0, t1, on_index=True)
    where = f"message @@ {_tsquery(mode)}{f}"
    orderby = {"relevance": "score DESC, line_no", "newest": "ts DESC, line_no DESC", "oldest": "ts, line_no"}[order]
    idx = ds["idx"]
    sql_rows = (f"SELECT {ds['cols']}, BM25({idx}.tableoid) AS score, "
                f"ts_highlight({idx}.message, 'StartSel=**, StopSel=**, MaxWords=40') AS message "
                f"FROM {idx} WHERE {where} ORDER BY {orderby} LIMIT %s")
    sql_cnt = f"SELECT count(*) FROM {idx} WHERE {where}"
    p = [query] + fp
    cnt, ms1 = _q(sql_cnt, p)
    rows, ms2 = _q(sql_rows, p + [limit]) if cnt[0][0] else ([], 0.0)
    names = [c.strip().split(" AS ")[-1] for c in ds["cols"].split(",")] + ["score", "message"]
    entries = [_entry(dataset, (names, r)) for r in rows]
    scope = DataScope(datasets=[dataset], time_from=time_from, time_to=time_to,
                      filters={k: v for k, v in dict(query=query, mode=mode, components=components, levels=levels,
                                                       sources=sources).items() if v})
    ev = _evidence("search_logs", [sql_rows, sql_cnt], [p + [limit], p], scope, cnt[0][0], len(entries), ms1 + ms2)
    md = _ev_md(ev)
    if not entries:
        md += ("No matching Log Entries. Try mode='any_words' or 'fuzzy', drop filters, or check spelling/"
               "Component names with describe_corpus.")
    for e in entries:
        md += f"- {e.ts} line {e.line_no} {e.source} {e.component} {e.level}: {e.message}\n"
    return _result(SearchResult(evidence=ev, entries=entries), md)


@mcp.tool(annotations=READ_ONLY)
def get_context(
    dataset: Dataset,
    line_no: Annotated[int, Field(description="line_no of the anchor Log Entry (from search_logs)")],
    scope: Annotated[Literal["same_source", "same_request", "same_instance", "time_window"], Field(
        description="same_source: same BGL node / OpenStack file; same_request / same_instance: OpenStack only; "
                    "time_window: all sources within +/- window_seconds")] = "same_source",
    before: Annotated[int, Field(ge=0, le=50)] = 10,
    after: Annotated[int, Field(ge=0, le=50)] = 10,
    window_seconds: Annotated[int, Field(ge=1, le=3600)] = 60,
) -> Annotated[CallToolResult, SearchResult]:
    """Log Entries just before and after one anchor entry, in time order: on the same node/source, the same
    OpenStack request or instance, or across all sources in a time window. Use it to see what happened around a
    failure. Output: entries (anchor flagged is_anchor) plus Evidence; total_count is the number of entries in
    the context scope, so truncated shows whether the window holds more."""
    ds = DATASETS[dataset]
    tbl = ds["table"]
    a, _ = _q(f"SELECT ts, {ds['source']}, " + ("request_id, instance_id" if dataset == "openstack" else "NULL, NULL")
              + f" FROM {tbl} WHERE line_no = %s", [line_no])
    if not a:
        raise ToolError(f"No {dataset} Log Entry with line_no={line_no}. Take line_no from search_logs results.")
    ats, asrc, areq, ainst = a[0]
    if scope in ("same_request", "same_instance") and dataset != "openstack":
        raise ToolError("same_request/same_instance exist only for dataset='openstack'; use same_source or time_window.")
    key = {"same_source": (ds["source"], asrc), "same_request": ("request_id", areq),
           "same_instance": ("instance_id", ainst)}.get(scope)
    if key and key[1] is None:
        raise ToolError(f"The anchor entry has no {key[0]}; use scope='same_source' or 'time_window'.")
    cols = ds["cols"] + ", message"
    if scope == "time_window":
        cond, cp = "ts BETWEEN %s AND %s", [ats - dt.timedelta(seconds=window_seconds), ats + dt.timedelta(seconds=window_seconds)]
    else:
        cond, cp = f"{key[0]} = %s", [key[1]]
    sql = (f"(SELECT {cols} FROM {tbl} WHERE {cond} AND (ts, line_no) < (%s, %s) ORDER BY ts DESC, line_no DESC LIMIT %s) "
           f"UNION ALL (SELECT {cols} FROM {tbl} WHERE {cond} AND (ts, line_no) >= (%s, %s) ORDER BY ts, line_no LIMIT %s) "
           "ORDER BY ts, line_no")
    p = cp + [ats, line_no, before] + cp + [ats, line_no, after + 1]
    sql_cnt = f"SELECT count(*) FROM {tbl} WHERE {cond}"
    rows, ms1 = _q(sql, p)
    cnt, ms2 = _q(sql_cnt, cp)
    names = [c.strip().split(" AS ")[-1] for c in cols.split(",")]
    entries = [_entry(dataset, (names, r), anchor=line_no) for r in rows]
    sc = DataScope(datasets=[dataset], filters={"anchor_line_no": line_no, "scope": scope,
                                                **({key[0]: key[1]} if key else {"window_seconds": window_seconds})})
    ev = _evidence("get_context", [sql, sql_cnt], [p, cp], sc, cnt[0][0], len(entries), ms1 + ms2)
    md = _ev_md(ev) + "".join(f"{'>>' if e.is_anchor else '- '} {e.ts} line {e.line_no} {e.source} {e.component} "
                              f"{e.level}: {e.message}\n" for e in entries)
    return _result(SearchResult(evidence=ev, entries=entries), md)


GROUPS = {"component": "component", "level": "level", "node": "node", "rack": "split_part(node, '-', 1)",
          "midplane": "split_part(node, '-', 1) || '-' || split_part(node, '-', 2)", "alert_tag": "alert_tag",
          "source_file": "source_file", "instance_id": "instance_id"}


@mcp.tool(annotations=READ_ONLY)
def count_by(
    group_by: Literal["component", "level", "node", "rack", "midplane", "alert_tag", "source_file", "instance_id", "dataset"],
    dataset: Literal["bgl", "openstack", "all"] = "all",
    query: Annotated[str | None, Field(description="Optional full-text filter (all words must appear)")] = None,
    time_from: str | None = None,
    time_to: str | None = None,
    components: list[str] | None = None,
    levels: list[str] | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 25,
) -> Annotated[CallToolResult, CountResult]:
    """Exact counts of Log Entries grouped by Component, level, node, rack, midplane, alert_tag (BGL), source_file
    or instance_id (OpenStack), or by dataset, over the whole Base Corpus or one Dataset, with optional full-text
    and time/Component/level filters. Use this to say HOW OFTEN something occurs. Counts are Log Entries, not
    incidents. Output: top `limit` groups by count, total_entries over all groups, Evidence (total_count = number
    of groups; truncated if more groups than returned)."""
    dsl = ["bgl", "openstack"] if dataset == "all" else [dataset]
    if group_by in ("node", "rack", "midplane", "alert_tag") and dsl != ["bgl"]:
        raise ToolError(f"group_by='{group_by}' exists only in BGL; pass dataset='bgl'.")
    if group_by in ("source_file", "instance_id") and dsl != ["openstack"]:
        raise ToolError(f"group_by='{group_by}' exists only in OpenStack; pass dataset='openstack'.")
    t0, t1 = _ts(time_from, "time_from"), _ts(time_to, "time_to")
    parts, p = [], []
    for d in dsl:
        key = f"'{d}'" if group_by == "dataset" else GROUPS[group_by]
        if query:
            f, fp = _filters(d, components, levels, None, t0, t1, on_index=True)
            parts.append(f"SELECT {key} AS k, count(*) AS n FROM {DATASETS[d]['idx']} "
                         f"WHERE message @@ websearch_to_tsquery(%s){f} GROUP BY 1")
            p += [query] + fp
        else:
            f, fp = _filters(d, components, levels, None, t0, t1, on_index=False)
            parts.append(f"SELECT {key} AS k, count(*) AS n FROM {DATASETS[d]['table']} WHERE true{f} GROUP BY 1")
            p += fp
    inner = " UNION ALL ".join(parts)
    sql = (f"WITH g AS (SELECT k, sum(n)::BIGINT AS n FROM ({inner}) GROUP BY k) "
           "SELECT k, n, (SELECT count(*) FROM g) AS n_groups, (SELECT sum(n) FROM g)::BIGINT AS total "
           "FROM g ORDER BY n DESC, k LIMIT %s")
    rows, ms = _q(sql, p + [limit])
    n_groups = rows[0][2] if rows else 0
    total = rows[0][3] if rows else 0
    groups = [Group(key=None if k is None else str(k), count=n, share=round(n / total, 4) if total else 0) for k, n, _, _ in rows]
    sc = DataScope(datasets=dsl, time_from=time_from, time_to=time_to,
                   filters={k: v for k, v in dict(group_by=group_by, query=query, components=components, levels=levels).items() if v})
    ev = _evidence("count_by", [sql], [p + [limit]], sc, n_groups, len(groups), ms)
    md = _ev_md(ev) + f"total_entries={total}\n" + "".join(f"- {g.key}: {g.count} ({g.share:.1%})\n" for g in groups)
    return _result(CountResult(evidence=ev, total_entries=total, groups=groups), md)


BUCKET = {"minute": dt.timedelta(minutes=1), "hour": dt.timedelta(hours=1), "day": dt.timedelta(days=1),
          "week": dt.timedelta(days=7)}
MAX_BUCKETS = 400


@mcp.tool(annotations=READ_ONLY)
def trend(
    dataset: Dataset,
    bucket: Literal["minute", "hour", "day", "week"] = "day",
    query: Annotated[str | None, Field(description="Optional full-text filter (all words must appear)")] = None,
    time_from: str | None = None,
    time_to: str | None = None,
    components: list[str] | None = None,
    levels: list[str] | None = None,
    sources: list[str] | None = None,
) -> Annotated[CallToolResult, TrendResult]:
    """Count of matching Log Entries per time bucket (gap-filled with zeros) for one Dataset, with optional
    full-text, Component, level and source filters. Use it to show whether a problem is rising, steady or a
    one-off burst over a stated period. At most 400 buckets: pick a coarser bucket or a shorter range otherwise.
    Output: buckets [{bucket, count}] plus Evidence (total_count = sum of counts)."""
    ds = DATASETS[dataset]
    t0, t1 = _ts(time_from, "time_from"), _ts(time_to, "time_to")
    if t0 is None or t1 is None:
        r, _ = _q(f"SELECT min(ts), max(ts) FROM {ds['table']}", [])
        t0 = t0 or r[0][0]
        t1 = t1 or (r[0][1] + dt.timedelta(microseconds=1))
    step = BUCKET[bucket]
    nb = (t1 - t0) / step
    if nb > MAX_BUCKETS:
        better = next((b for b, s in BUCKET.items() if (t1 - t0) / s <= MAX_BUCKETS), "week")
        raise ToolError(f"{nb:.0f} {bucket} buckets exceed the limit of {MAX_BUCKETS}. Use bucket='{better}' "
                        f"or narrow time_from/time_to.")
    iv = f"{int(step.total_seconds())} seconds"
    if query:
        f, fp = _filters(dataset, components, levels, sources, t0, t1, on_index=True)
        src, where, wp = ds["idx"], f"message @@ websearch_to_tsquery(%s){f}", [query] + fp
    else:
        f, fp = _filters(dataset, components, levels, sources, t0, t1, on_index=False)
        src, where, wp = ds["table"], f"true{f}", fp
    sql = ("WITH b AS (SELECT unnest(generate_series(time_bucket(%s::INTERVAL, %s::TIMESTAMP), %s::TIMESTAMP, %s::INTERVAL)) AS bucket), "
           f"c AS (SELECT time_bucket(%s::INTERVAL, ts) AS bucket, count(*) AS n FROM {src} WHERE {where} GROUP BY 1) "
           "SELECT b.bucket, coalesce(c.n, 0) FROM b LEFT JOIN c USING (bucket) WHERE b.bucket < %s ORDER BY 1")
    p = [iv, t0, t1, iv, iv] + wp + [t1]
    rows, ms = _q(sql, p)
    buckets = [Bucket(bucket=str(b), count=n) for b, n in rows]
    total = sum(b.count for b in buckets)
    sc = DataScope(datasets=[dataset], time_from=str(t0), time_to=str(t1),
                   filters={k: v for k, v in dict(bucket=bucket, query=query, components=components, levels=levels, sources=sources).items() if v})
    sc.filters["total_entries"] = total
    ev = _evidence("trend", [sql], [p], sc, len(buckets), len(buckets), ms)
    nz = [b for b in buckets if b.count]
    peak = max(buckets, key=lambda b: b.count) if buckets else None
    md = _ev_md(ev) + (f"{len(buckets)} buckets, {len(nz)} non-zero, peak {peak.count} at {peak.bucket}\n" if peak else "") \
        + "".join(f"{b.bucket[:16]} {b.count}\n" for b in nz)
    return _result(TrendResult(evidence=ev, bucket=bucket, buckets=buckets), md)


if __name__ == "__main__":
    mcp.run("stdio")
