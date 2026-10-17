"""`get_trace` (T21): one OTel trace across signals, its spans, its Log Entries and its services' metrics.

otel_demo's logs, traces and metrics are remote Parquet files of one OpenTelemetry demo run that
share TraceId (`corpus.datasets`, `make otel-setup`). Five statements, each a read-only `db.fetch_all`:

1. the trace's spans per service (counts, Error spans, first start, last end): the trace window;
2. the spans themselves, ranked so the Error spans with no Error span below them (where the failure
   started) and the largest self time (where the time went) are always kept, with depth and self
   time computed in SQL over the trace's own spans (a recursive CTE);
3. the trace's Log Entries per Component and level;
4. the Log Entries themselves, error levels first;
5. the metrics of the trace's services in the minutes around it, from the local per-minute roll-up,
   each with the baseline of its series over the whole run, most unusual first. Unusual series whose
   baseline varies rank by their distance from the median in units of the p05-to-p95 range; series
   whose baseline never varied (limits, queues that sit at 0) follow, by relative change, so that a
   flat series moving off 0 (an infinite number of ranges) cannot bury a real spike.

Statements 1 to 4 read the remote files. None is sorted by TraceId or time, but both carry TraceId
bloom filters, so `trace_id = %s` reads only the row groups that hold the trace. That is still
slow on a cold cache: about 20 MB per row group from S3 (measured on the shared dev server, which
reads S3 at about 7 MB/s: 648 MB and 97 s of server time for a trace in 23 of 354 span row groups and
6 of 120 log row groups, about 2 s once cached). So they run with `SLM_TRACE_TIMEOUT_S` (per
statement) instead of the default limit. Keeping spans and Log Entries in two statements each costs
no extra bytes (each reads the columns it needs; merging them read as many) and keeps every cold
statement near 40 s. Statement 5 is local and fast.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp.config import Role, get_settings
from serenedb_log_mcp.corpus.datasets import Relation, relation
from serenedb_log_mcp.db import QueryFailed, QueryResult, QueryTimeout, fetch_all
from serenedb_log_mcp.evidence import DataScope, RoleParam, ScopeFilters
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool

DATASET = "otel_demo"
TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_SPANS, MAX_SPANS = 50, 200
DEFAULT_LOGS, MAX_LOGS = 20, 50
DEFAULT_METRICS, MAX_METRICS = 20, 50
DEFAULT_PAD, MAX_PAD = 2, 30
MESSAGE_CHARS = 240
ERROR_LEVELS = ("error", "fatal", "critical")
UNUSUAL = ("unusual_high", "unusual_low")
MIN_BASELINE_MINUTES = 30
FLAG_TOLERANCE = 1e-9
"""Relative slack before a window value counts as outside p05..p95: the mean over a window of a flat
series comes out a rounding error above the series' p95 (0.05000000000000001 > 0.05)."""
UNDEFINED_TABLE = "42P01"

COLUMNS = (
    "signal",
    "ts",
    "service",
    "name",
    "kind",
    "status",
    "duration_ms",
    "self_ms",
    "depth",
    "span_id",
    "parent_span_id",
    "text",
    "value",
    "unit",
    "baseline_p05",
    "baseline_median",
    "baseline_p95",
)

NOT_SET_UP = (
    "otel_demo is registered but not set up in this database (its views over the remote Parquet files "
    "are missing). Run `make otel-setup` once with the server's SLM_SCHEMA (it creates the views, the "
    "facts and the metric roll-up), then call get_trace again."
)
TIMEOUT = (
    "Reading trace {trace} from the remote otel_demo files ran longer than the {limit:g} s limit "
    "(SLM_TRACE_TIMEOUT_S) and was cancelled. A cold lookup reads the TraceId bloom filters of the log "
    "and trace files (about 425 MiB) and about 20 MB for every row group that holds the trace from S3: "
    "on the shared dev server one statement took up to 40 s for a trace in 23 row groups, and the whole "
    "lookup about 2 s once cached. Run `make otel-setup` (it warms the bloom filters) or raise "
    "SLM_TRACE_TIMEOUT_S, then call get_trace again."
)
ROLL_UP_MISSING = (
    "No metric rows: the per-minute metric roll-up is missing in this database. Run `make otel-setup` "
    "to compute it; the spans and Log Entries above do not depend on it."
)
READ_NOTE = (
    "Spans and Log Entries were read in place from the remote Parquet files; their TraceId bloom filters "
    "limit the read to the row groups that hold this trace."
)
ORIGIN_NOTE = (
    "error_origin_spans are Error spans with no Error span below them in this trace: the deepest point "
    "where the request failed. Where the error surfaced is an Observed Fact; why it failed is a Hypothesis."
)
METRICS_NOTE = (
    "Metric rows: the services on this trace, {start} to {end} (the trace's minutes widened by "
    "pad_minutes), from the per-minute roll-up. value: gauge = mean data point, counter = mean "
    "increase per minute, histogram = mean observation; baseline_p05, baseline_median and baseline_p95 "
    "are the same series' per-minute values over the whole run, and status unusual_high or unusual_low "
    "means outside p05 to p95. A state that lasts a large part of the run (more than 5% of its minutes) "
    "is part of its own baseline and reads normal, so compare value with baseline_median too. A metric "
    "that moved in these minutes is consistent with the failure; it does not establish a cause."
)


def register(server: MCPServer) -> None:
    """Register `get_trace` on `server`."""

    @evidence_tool(server, title="Follow one trace across spans, Log Entries and metrics")
    def get_trace(
        role: RoleParam,
        trace_id: Annotated[
            str,
            Field(
                description="The trace_id of an otel_demo Log Entry or span: 32 hexadecimal characters, "
                "for example 'b60c4584ee2689aaa9751261d1b9d800'.",
                max_length=64,
            ),
        ],
        span_limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_SPANS,
                description=f"Most spans to return (default {DEFAULT_SPANS}, at most {MAX_SPANS}); Error "
                "spans and the largest self time are kept first.",
            ),
        ] = DEFAULT_SPANS,
        log_limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_LOGS,
                description=f"Most Log Entries to return (default {DEFAULT_LOGS}, at most {MAX_LOGS}); "
                "error levels are kept first.",
            ),
        ] = DEFAULT_LOGS,
        metric_limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_METRICS,
                description=f"Most metric series to return (default {DEFAULT_METRICS}, at most "
                f"{MAX_METRICS}); the most unusual against their baseline come first.",
            ),
        ] = DEFAULT_METRICS,
        pad_minutes: Annotated[
            int,
            Field(
                ge=0,
                le=MAX_PAD,
                description=f"Whole minutes added before and after the trace for the metrics (default "
                f"{DEFAULT_PAD}, at most {MAX_PAD}).",
            ),
        ] = DEFAULT_PAD,
    ) -> ToolOutcome:
        """Follow one request across signals: the spans of one OTel trace, the Log Entries with its
        trace_id and the metrics of its services in the minutes around it (Dataset otel_demo).

        Purpose: from an error Log Entry that carries a trace_id, see which service and span failed
        or was slow, what every service logged for that request, and whether those services'
        metrics were unusual at that moment. Use get_trace instead of get_context whenever the Log
        Entry has a trace_id: the trace links exactly the spans and Log Entries of that one request
        across all services, while get_context returns what happened nearby on the same source or in
        a time window, including unrelated requests. Use get_context for BGL and OpenStack, which
        have no traces, or for what else a service logged around the failure. One trace is one
        example, not a frequency: use count_by or trend on otel_demo for how often errors occur.
        Reads are remote: a lookup the server has not cached reads about 20 MB from S3 for every
        row group that holds the trace (about 100 s for a 30-service-hop trace on the shared dev
        server); a cached one takes about 2 s.
        Inputs: role, trace_id, and optional span_limit, log_limit, metric_limit, pad_minutes.
        Outputs: the Evidence envelope. rows: one per span, Log Entry or metric series, told apart
        by signal ('span', 'log', 'metric'), with columns signal, ts, service, name, kind, status,
        duration_ms, self_ms, depth, span_id, parent_span_id, text, value, unit, baseline_p05,
        baseline_median, baseline_p95. Span rows (by start time): name is the operation, kind
        Client, Server, Internal, Producer or Consumer, status Ok, Error or Unset, duration_ms,
        self_ms (its duration minus its child spans: where the time went), depth (0 is the root),
        span_id, parent_span_id, text the status message. Log rows (by time): status is the level,
        span_id the span that wrote it, text the message (first 240 characters). Metric rows (most
        unusual first): ts is the window start, name the metric, kind gauge (value = mean data
        point), counter (mean increase per minute) or histogram (mean observation), value over the
        window, unit, baseline_* the series' per-minute p05, median and p95 over the whole run, and
        status unusual_high, unusual_low, normal or no_baseline. summary: trace_start, trace_end,
        trace_duration_ms, spans_total, error_spans, services (spans and error_spans each),
        error_origin_spans (Error spans with no Error span below them), slowest_self_span,
        log_entries_total, log_levels, error_log_entries, metrics_window, metric_series_total and
        metric_series_unusual. total_matches counts spans plus Log Entries plus metric series.
        """
        trace = normalise_trace_id(trace_id)
        rel = relation(DATASET, role)
        results: list[QueryResult] = []

        services_result = _remote(services_statement(rel), [trace], role, trace)
        results.append(services_result)
        services = [_service(row) for row in services_result.rows]
        spans_total = sum(entry["spans"] for entry in services)
        span_rows: list[tuple[Any, ...]] = []
        if spans_total:
            spans_result = _remote(spans_statement(rel), [trace, span_limit], role, trace)
            results.append(spans_result)
            span_rows = list(spans_result.rows)

        levels_result = _remote(log_levels_statement(rel), [trace], role, trace)
        results.append(levels_result)
        log_groups = list(levels_result.rows)
        logs_total = sum(int(row[2]) for row in log_groups)
        log_rows: list[tuple[Any, ...]] = []
        if logs_total:
            logs_result = _remote(logs_statement(rel), [trace, log_limit], role, trace)
            results.append(logs_result)
            log_rows = list(logs_result.rows)

        notes: list[str] = []
        trace_start = min((entry["first"] for entry in services), default=None)
        trace_end = max((entry["last"] for entry in services), default=None)
        if spans_total:
            metric_services = [entry["service"] for entry in services]
            window_of = (trace_start, trace_end)
        else:
            metric_services = list(dict.fromkeys(str(row[0]) for row in log_groups))
            window_of = (
                min((row[3] for row in log_groups), default=None),
                max((row[4] for row in log_groups), default=None),
            )
        window = None
        metric_rows: list[tuple[Any, ...]] = []
        if window_of[0] is not None and window_of[1] is not None and metric_services:
            window = metrics_window(window_of[0], window_of[1], pad_minutes)
            params = [metric_services, window[0], window[1], metric_limit]
            try:
                metrics_result = fetch_all(metrics_statement(rel), params, role=role)
            except QueryFailed as exc:
                if exc.sqlstate != UNDEFINED_TABLE:
                    raise
                notes.append(ROLL_UP_MISSING)
            else:
                results.append(metrics_result)
                metric_rows = list(metrics_result.rows)

        metric_total = int(metric_rows[0][10]) if metric_rows else 0
        rows = [
            *(_span_row(row) for row in sorted(span_rows, key=lambda row: (row[0], row[7] or 0))),
            *(_log_row(row) for row in sorted(log_rows, key=lambda row: row[0])),
            *(_metric_row(row, window[0]) for row in metric_rows if window is not None),
        ]
        total = spans_total + logs_total + metric_total
        summary = _summary(trace, services, span_rows, log_groups, len(log_rows), metric_rows, window)
        summary["trace_start"] = _iso(trace_start)
        summary["trace_end"] = _iso(trace_end)
        summary["trace_duration_ms"] = (
            round((trace_end - trace_start).total_seconds() * 1000, 3) if trace_start and trace_end else None
        )

        if not spans_total and not logs_total:
            notes.append(
                f"No span and no Log Entry in otel_demo has trace_id {trace}. Check that it is the "
                "32-character trace_id of an otel_demo Log Entry or span; Log Entries with an empty "
                "trace_id belong to no trace."
            )
        else:
            notes.append(READ_NOTE)
        if not spans_total and logs_total:
            notes.append(
                "No span in the traces file has this trace_id (only Log Entries do), so the metric "
                "window is the time of its Log Entries."
            )
        if summary["error_origin_spans"]:
            notes.append(ORIGIN_NOTE)
        if window is not None and (metric_rows or not any(note == ROLL_UP_MISSING for note in notes)):
            notes.append(METRICS_NOTE.format(start=_iso(window[0]), end=_iso(window[1])))

        scope = DataScope(
            datasets=[DATASET],
            filters=ScopeFilters.of(
                trace_id=trace,
                metrics_window=None if window is None else {"from": _iso(window[0]), "to": _iso(window[1])},
                metrics_services=metric_services or None,
                pad_minutes=pad_minutes,
            ),
        )
        return ToolOutcome(
            result=QueryResult.combine(*results, total=total, rows=rows),
            scope=scope,
            columns=COLUMNS,
            summary=summary,
            notes=notes,
            rows=rows,
            total_matches=total,
            truncation_note=_truncation_note(
                (len(span_rows), spans_total), (len(log_rows), logs_total), (len(metric_rows), metric_total)
            ),
        )


def normalise_trace_id(value: str) -> str:
    """The trace id in lower case; ScopeError unless it is 32 hexadecimal characters."""
    trace = value.strip().lower()
    if not TRACE_ID_RE.fullmatch(trace):
        raise ScopeError(
            f"trace_id {value.strip()!r} is not a trace id: it must be 32 hexadecimal characters without "
            "dashes, for example 'b60c4584ee2689aaa9751261d1b9d800', as in the trace_id of an otel_demo "
            "Log Entry."
        )
    return trace


def metrics_window(start: datetime, end: datetime, pad_minutes: int) -> tuple[datetime, datetime]:
    """The whole minutes from the trace's first minute to its last, widened by `pad_minutes` each side."""
    pad = timedelta(minutes=pad_minutes)
    first = start.replace(second=0, microsecond=0) - pad
    last = end.replace(second=0, microsecond=0) + timedelta(minutes=1) + pad
    return first, last


# SQL ------------------------------------------------------------------------------------------------


def services_statement(rel: Relation) -> str:
    """Per service of one trace: spans, Error spans, first start and last end."""
    return (
        sql.SQL(
            """SELECT service, count(*) AS spans, count(*) FILTER (WHERE status = 'Error') AS error_spans,
       min(ts) AS first_start, max(ts + to_microseconds(duration_ns // 1000)) AS last_end
FROM {spans}
WHERE trace_id = %s
GROUP BY service
ORDER BY first_start, service"""
        )
        .format(spans=rel.signal("spans"))
        .as_string(None)
    )


def spans_statement(rel: Relation) -> str:
    """The spans of one trace with depth, self time and the error-origin flag, the key spans first."""
    return (
        sql.SQL(
            """WITH RECURSIVE s AS (
    SELECT ts, span_id, parent_span_id, service, operation, kind, duration_ns, status, status_message
    FROM {spans}
    WHERE trace_id = %s
), tree(span_id, depth, path) AS (
    SELECT span_id, 0, [span_id] FROM s WHERE parent_span_id NOT IN (SELECT span_id FROM s)
    UNION ALL
    SELECT s.span_id, tree.depth + 1, list_append(tree.path, s.span_id)
    FROM s JOIN tree ON s.parent_span_id = tree.span_id
    WHERE tree.depth < 64
), children AS (
    SELECT parent_span_id AS span_id, sum(duration_ns) AS child_ns FROM s GROUP BY parent_span_id
), spans AS (
    SELECT s.*, tree.depth, tree.path, greatest(s.duration_ns - coalesce(children.child_ns, 0), 0) AS self_ns
    FROM s
    LEFT JOIN tree ON tree.span_id = s.span_id
    LEFT JOIN children ON children.span_id = s.span_id
), marked AS (
    SELECT spans.*,
           spans.status = 'Error' AND NOT EXISTS (
               SELECT 1 FROM spans AS below
               WHERE below.status = 'Error' AND below.span_id <> spans.span_id
                 AND list_contains(below.path, spans.span_id)
           ) AS error_origin,
           row_number() OVER (ORDER BY spans.self_ns DESC, spans.ts) = 1 AS slowest
    FROM spans
)
SELECT ts, service, operation, kind, status, round(duration_ns / 1e6, 3) AS duration_ms,
       round(self_ns / 1e6, 3) AS self_ms, depth, span_id, parent_span_id,
       left(status_message, {chars}) AS status_message, error_origin,
       count(*) FILTER (WHERE error_origin) OVER () AS error_origins, slowest
FROM marked
ORDER BY error_origin DESC, slowest DESC, status = 'Error' DESC, self_ns DESC, ts
LIMIT %s"""
        )
        .format(spans=rel.signal("spans"), chars=sql.Literal(MESSAGE_CHARS))
        .as_string(None)
    )


def log_levels_statement(rel: Relation) -> str:
    """Per Component and level of one trace: Log Entries, first and last time."""
    return (
        sql.SQL(
            """SELECT {component}, {level}, count(*) AS n, min({ts}) AS first, max({ts}) AS last
FROM {logs}
WHERE {trace} = %s
GROUP BY component, level
ORDER BY first, component, level"""
        )
        .format(
            logs=rel.table,
            component=rel.column("component"),
            level=rel.column("level"),
            ts=rel.column("ts_exact"),
            trace=rel.column("trace"),
        )
        .as_string(None)
    )


def logs_statement(rel: Relation) -> str:
    """The Log Entries of one trace, error levels first, then by time."""
    return (
        sql.SQL(
            """SELECT {ts}, {component}, {level}, {span}, left({message}, {chars}) AS message
FROM {logs}
WHERE {trace} = %s
ORDER BY lower({level}) IN ({errors}) DESC, {ts}
LIMIT %s"""
        )
        .format(
            logs=rel.table,
            ts=rel.column("ts_exact"),
            component=rel.column("component"),
            level=rel.column("level"),
            span=rel.column("span"),
            message=rel.column("message"),
            trace=rel.column("trace"),
            chars=sql.Literal(MESSAGE_CHARS),
            errors=sql.SQL(", ").join(sql.Literal(level) for level in ERROR_LEVELS),
        )
        .as_string(None)
    )


def metrics_statement(rel: Relation) -> str:
    """The services' metric series over a window, judged against their whole-run baselines."""
    return (
        sql.SQL(
            """WITH w AS (
    SELECT service, metric, unit, kind, count(*) AS minutes,
           CASE WHEN kind = 'counter' THEN avg(value) ELSE sum(total) / nullif(sum(n), 0) END AS value
    FROM {minutes}
    WHERE service = ANY(%s::VARCHAR[]) AND minute >= %s AND minute < %s
    GROUP BY service, metric, unit, kind
), judged AS (
    SELECT w.service, w.metric, w.unit, w.kind, w.value, b.p05, b.median, b.p95, w.minutes,
           CASE WHEN w.value IS NULL OR coalesce(b.minutes, 0) < {min_minutes} THEN 'no_baseline'
                WHEN w.value > b.p95 + {tolerance} * greatest(abs(b.p95), 1) THEN 'unusual_high'
                WHEN w.value < b.p05 - {tolerance} * greatest(abs(b.p05), 1) THEN 'unusual_low'
                ELSE 'normal' END AS flag,
           b.p95 > b.p05 AS varies,
           CASE WHEN b.p95 > b.p05 THEN abs(w.value - b.median) / (b.p95 - b.p05)
                ELSE abs(w.value - b.median) / greatest(abs(b.median), 1e-9) END AS deviation
    FROM w
    LEFT JOIN {baselines} AS b
      ON b.service = w.service AND b.metric = w.metric AND b.unit = w.unit AND b.kind = w.kind
)
SELECT service, metric, unit, kind, round(value, 6) AS value, round(p05, 6) AS p05,
       round(median, 6) AS median, round(p95, 6) AS p95, flag, minutes,
       count(*) OVER () AS series_total,
       count(*) FILTER (WHERE flag IN ({unusual})) OVER () AS unusual_total
FROM judged
ORDER BY flag IN ({unusual}) DESC, varies DESC NULLS LAST, deviation DESC NULLS LAST, service, metric
LIMIT %s"""
        )
        .format(
            minutes=rel.signal("metric_minutes"),
            baselines=rel.signal("metric_baselines"),
            min_minutes=sql.Literal(MIN_BASELINE_MINUTES),
            tolerance=sql.Literal(FLAG_TOLERANCE),
            unusual=sql.SQL(", ").join(sql.Literal(flag) for flag in UNUSUAL),
        )
        .as_string(None)
    )


# Shaping --------------------------------------------------------------------------------------------


def _remote(statement: str, params: Sequence[Any], role: Role, trace: str) -> QueryResult:
    """One statement over the remote files, with the trace timeout and errors that say what to do."""
    limit = get_settings().trace_timeout_s
    try:
        return fetch_all(statement, params, role=role, timeout_s=limit)
    except QueryTimeout as exc:
        raise ToolError(TIMEOUT.format(trace=trace, limit=limit)) from exc
    except QueryFailed as exc:
        if exc.sqlstate == UNDEFINED_TABLE:
            raise ScopeError(NOT_SET_UP) from exc
        raise


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _service(row: Sequence[Any]) -> dict[str, Any]:
    service, spans, errors, first, last = row
    return {"service": service, "spans": int(spans), "error_spans": int(errors), "first": first, "last": last}


def _span_row(row: Sequence[Any]) -> list[Any]:
    ts, service, operation, kind, status, duration_ms, self_ms, depth, span_id, parent, message = row[:11]
    return [
        "span", _iso(ts), service, operation, kind, status, duration_ms, self_ms, depth, span_id,
        parent or None, message or None, None, None, None, None, None,
    ]  # fmt: skip


def _log_row(row: Sequence[Any]) -> list[Any]:
    ts, component, level, span_id, message = row
    return [
        "log", _iso(ts), component, None, None, level, None, None, None, span_id or None, None, message,
        None, None, None, None, None,
    ]  # fmt: skip


def _metric_row(row: Sequence[Any], window_start: datetime) -> list[Any]:
    service, metric, unit, kind, value, p05, median, p95, flag = row[:9]
    return [
        "metric", _iso(window_start), service, metric, kind, flag, None, None, None, None, None, None,
        value, unit, p05, median, p95,
    ]  # fmt: skip


def _summary(
    trace: str,
    services: list[dict[str, Any]],
    span_rows: list[tuple[Any, ...]],
    log_groups: list[tuple[Any, ...]],
    logs_returned: int,
    metric_rows: list[tuple[Any, ...]],
    window: tuple[datetime, datetime] | None,
) -> dict[str, Any]:
    levels: dict[str, int] = {}
    for _, level, n, _, _ in log_groups:
        levels[str(level)] = levels.get(str(level), 0) + int(n)
    origins = [row for row in span_rows if row[11]]
    slowest = next((row for row in span_rows if row[13]), None)
    return {
        "trace_id": trace,
        "spans_total": sum(entry["spans"] for entry in services),
        "spans_returned": len(span_rows),
        "error_spans": sum(entry["error_spans"] for entry in services),
        "services": [
            {"service": entry["service"], "spans": entry["spans"], "error_spans": entry["error_spans"]}
            for entry in services
        ],
        "error_origin_spans": [
            {
                "span_id": row[8],
                "service": row[1],
                "operation": row[2],
                "depth": row[7],
                "status_message": row[10],
            }
            for row in origins
        ],
        "slowest_self_span": None
        if slowest is None
        else {
            "span_id": slowest[8],
            "service": slowest[1],
            "operation": slowest[2],
            "self_ms": slowest[6],
            "duration_ms": slowest[5],
        },
        "log_entries_total": sum(levels.values()),
        "log_entries_returned": logs_returned,
        "log_levels": levels,
        "error_log_entries": sum(n for level, n in levels.items() if level.lower() in ERROR_LEVELS),
        "metrics_window": None if window is None else {"from": _iso(window[0]), "to": _iso(window[1])},
        "metric_series_total": int(metric_rows[0][10]) if metric_rows else 0,
        "metric_series_returned": len(metric_rows),
        "metric_series_unusual": int(metric_rows[0][11]) if metric_rows else 0,
    }


def _truncation_note(spans: tuple[int, int], logs: tuple[int, int], metrics: tuple[int, int]) -> str | None:
    parts = []
    if spans[0] < spans[1]:
        parts.append(f"{spans[0]} of {spans[1]:,} spans (Error spans first, then the largest self time)")
    if logs[0] < logs[1]:
        parts.append(f"{logs[0]} of {logs[1]:,} Log Entries (error levels first)")
    if metrics[0] < metrics[1]:
        parts.append(f"{metrics[0]} of {metrics[1]:,} metric series (the most unusual first)")
    if not parts:
        return None
    return (
        f"Showing {'; '.join(parts)}. summary holds the exact totals; raise span_limit, log_limit or "
        "metric_limit to see more."
    )
