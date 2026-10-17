"""Multimodal investigation (T21): from an error Log Entry to its trace, the failing span and the metrics.

Usage (see `make help`):
    uv run python bench/multimodal.py [--out docs/results/multimodal.md] [--server-note TEXT]
    uv run python bench/multimodal.py --from-json docs/results/multimodal.json  (render only)

Against DATABASE_URL and SLM_SCHEMA (`make bench-multimodal` uses the huge schema, where `make
otel-setup` registered otel_demo), it runs one Engineer investigation through the product's tools and
writes the report (`--out`), the measured run as JSON (`.json`, which `--from-json` renders again
without measuring) and the Evidence Ledger of the run (`.ledger.jsonl`), so every number the report
cites can be checked against the stored Evidence:

    1. Data: HTTP HEAD (Content-Length) and the Parquet footer (rows, row groups) of every otel_demo
       file, and the size of the local metric roll-up.
    2. E1 count_by: error Log Entries per service over the whole run.
    3. E2 trend: the followed service's error Log Entries per hour, and its peak hour; E3 count_by over
       the burst (the hours with at least a tenth of the peak), with its previous-period baseline.
    4. The error Log Entry: the last error Log Entry of that service in the burst's last hour that
       carries a trace_id (the most recent failure), in two phases (row numbers from narrow columns,
       then only those rows). This is the search step; once search_logs (T5) is merged it is a tool
       call.
    5. The trace: EXPLAIN ANALYZE of get_trace's four remote statements for that trace first (row
       groups read, bytes from S3, server time of the first read), then E4 get_trace itself.
    6. The report: the steps, the Evidence, the answer in four sections, the reads and the environment.

It reads only what the tools read; it never indexes anything or copies a row.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import platform
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from huge_scale import Plan, Session, Tools, content_length, fmt_bytes, fmt_ms
from index_tradeoff import BenchError
from serenedb_log_mcp import ledger
from serenedb_log_mcp.config import ConfigError, Settings, load_settings, set_settings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.tools import get_trace as trace_tool

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "results" / "multimodal.md"
LEVEL = "error"
SETUP_MEASURED = (
    "`make otel-setup` took 85 s on a cold file cache on 2026-10-17 (82.75 s of it the metric roll-up, "
    "which reads about 600 MB of metric columns once) and 10 s when the files were cached"
)
COLD_MEASURED = (
    "The first run of this report on 2026-10-17 met a cold cache: get_trace's four remote statements "
    "for trace f6206a8a53df0b7d2e47a9cc7ebf5b11 (23 of 354 span row groups, 6 of 120 log row groups) "
    "fetched 647.60 MB from S3 in 97.37 s of server time, at most 38.21 s for one statement; the same "
    "call took 2.04 s right after"
)
SIGNAL_TITLES = {
    "logs": "Log Entries",
    "traces": "spans",
    "metrics_gauge": "gauge data points",
    "metrics_sum": "sum data points",
    "metrics_histogram": "histogram data points",
}


@dataclass(frozen=True)
class FileInfo:
    signal: str
    url: str
    size: int | None
    rows: int
    row_groups: int


@dataclass(frozen=True)
class Read:
    """One remote statement, measured by EXPLAIN ANALYZE on its first run in this report."""

    label: str
    statement: str
    plan: Plan


@dataclass
class Report:
    files: list[FileInfo]
    roll_up_rows: int
    roll_up_series: int
    count_by: dict[str, Any]
    trend: dict[str, Any]
    burst: dict[str, Any]
    entry: dict[str, Any]
    selection: list[Read]
    trace: dict[str, Any]
    trace_reads: list[Read]
    environment: dict[str, str]
    ledger_file: str
    notes: list[str] = field(default_factory=list)


# Pure parts -----------------------------------------------------------------------------------------


def selection_statements(logs_url: str, service: str, hour: datetime) -> tuple[str, str]:
    """The two phases that find the last error Log Entry with a trace_id of `service` in `hour`.

    Phase one reads only narrow columns (TimestampTime, ServiceName, SeverityText) of every row
    group and returns row numbers; the caller passes them to phase two (`entry_statement`), which
    reads the wide columns of only the row groups that hold them.
    """
    source = sql.SQL("read_parquet({}, file_row_number = true)").format(sql.Literal(logs_url))
    start = sql.Literal(hour.strftime("%Y-%m-%d %H:%M:%S+00"))
    end = sql.Literal((hour + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S+00"))
    first = sql.SQL(
        'SELECT file_row_number FROM {source} WHERE "ServiceName" = {service} AND "SeverityText" = {level} '
        'AND "TimestampTime" >= CAST({start} AS TIMESTAMPTZ) AND "TimestampTime" < CAST({end} AS '
        "TIMESTAMPTZ) "
        'ORDER BY "TimestampTime" DESC, file_row_number DESC LIMIT 4'
    ).format(source=source, service=sql.Literal(service), level=sql.Literal(LEVEL), start=start, end=end)
    return first.as_string(None), source.as_string(None)


def entry_statement(source: str, row_numbers: Sequence[int]) -> str:
    """Phase two: the picked rows, wide columns included."""
    return (
        sql.SQL(
            'SELECT file_row_number, CAST("Timestamp" AS TIMESTAMP) AS ts, "ServiceName", "SeverityText", '
            '"TraceId", "SpanId", "Body" FROM {source} WHERE file_row_number IN ({rows}) ORDER BY '
            "file_row_number"
        )
        .format(source=sql.SQL(source), rows=sql.SQL(", ").join(sql.Literal(n) for n in row_numbers))
        .as_string(None)
    )


def row_dicts(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(zip(evidence["columns"], row, strict=True)) for row in evidence["rows"]]


def peak(trend: dict[str, Any]) -> tuple[str, int]:
    series = trend["summary"]["series"][0]
    return str(series["peak_bucket"]), int(series["peak_count"])


def burst_window(trend: dict[str, Any]) -> tuple[datetime, datetime]:
    """From the first to the end of the last hour with at least a tenth of the peak's error Log Entries."""
    _, peak_count = peak(trend)
    busy = [row["bucket"] for row in row_dicts(trend) if row["(all)"] >= peak_count / 10]
    return datetime.fromisoformat(busy[0]), datetime.fromisoformat(busy[-1]) + timedelta(hours=1)


def selection_hour(burst: dict[str, Any]) -> datetime:
    """The last hour of the burst E3 counted: where the report picks its request (the latest failure)."""
    return datetime.fromisoformat(burst["data_scope"]["time_to"]) - timedelta(hours=1)


def to_json(report: Report) -> dict[str, Any]:
    """The measured run, so the report can be rendered again without measuring again (`--from-json`)."""
    return dataclasses.asdict(report)


def from_json(raw: dict[str, Any]) -> Report:
    def read(item: dict[str, Any]) -> Read:
        plan = dict(item["plan"])
        if plan.get("row_groups") is not None:
            plan["row_groups"] = tuple(plan["row_groups"])
        return Read(item["label"], item["statement"], Plan(**plan))

    return Report(
        **{
            **raw,
            "files": [FileInfo(**item) for item in raw["files"]],
            "selection": [read(item) for item in raw["selection"]],
            "trace_reads": [read(item) for item in raw["trace_reads"]],
        }
    )


def furthest_from_median(trace: dict[str, Any], service: str) -> dict[str, Any] | None:
    """The service's returned metric row whose value is the most times its median (median above 0)."""
    rows = [
        row
        for row in row_dicts(trace)
        if row["signal"] == "metric" and row["service"] == service and (row["baseline_median"] or 0) > 0
    ]
    return max(
        rows,
        key=lambda row: max(
            row["value"] / row["baseline_median"], row["baseline_median"] / max(row["value"], 1e-12)
        ),
        default=None,
    )


def _num(value: float) -> str:
    """A metric value for prose: whole numbers with separators when large, 6 significant digits else."""
    return f"{value:,.0f}" if abs(value) >= 1e5 else f"{value:,.6g}"


def _names(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _unit(unit: str | None) -> str:
    """A metric unit for prose: none for the dimensionless '1' (OTel's unit of a ratio)."""
    return "" if unit in (None, "", "1") else f" {unit}"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.6g}" if abs(value) < 1e6 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: Sequence[str], rows: Sequence[Sequence[Any]], right: Sequence[int] = ()) -> list[str]:
    align = ["---:" if i in right else "---" for i in range(len(header))]
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(align) + " |",
        *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows),
    ]


def _short(statement: str, limit: int = 400) -> str:
    text = " ".join(statement.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plan_cells(plan: Plan) -> list[str]:
    groups = f"{plan.row_groups[0]:,} / {plan.row_groups[1]:,}" if plan.row_groups else "n/a"
    return [groups, fmt_bytes(plan.http_in or 0), fmt_ms(plan.total_ms)]


def answer(report: Report) -> list[str]:
    """The Engineer's answer in the four sections, every number from the cited Evidence."""
    e1, e2, e3, e4 = report.count_by, report.trend, report.burst, report.trace
    by_service = {row["component"]: row["count"] for row in row_dicts(e1)}
    service, count = next(iter(by_service.items()))
    picked = report.entry["service"]
    peak_bucket, peak_count = peak(e2)
    burst = row_dicts(e3)[0]
    burst_from = datetime.fromisoformat(e3["data_scope"]["time_from"])
    burst_to = datetime.fromisoformat(e3["data_scope"]["time_to"])
    burst_hours = round((burst_to - burst_from).total_seconds() / 3600)
    summary = e4["summary"]
    origin = summary["error_origin_spans"][0] if summary["error_origin_spans"] else None
    spans = [row for row in row_dicts(e4) if row["signal"] == "span"]
    failing = [row for row in spans if row["status"] == "Error" and row["text"]]
    metrics = [row for row in row_dicts(e4) if row["signal"] == "metric" and row["status"] != "normal"]
    slowest = summary["slowest_self_span"]
    ids = {name: report_id(evidence) for name, evidence in (("e1", e1), ("e2", e2), ("e3", e3), ("e4", e4))}
    lines = ["### Observed Facts", ""]
    lines.append(
        f"- Over the whole run, {service} wrote the most Log Entries at level {LEVEL} ({count:,}) and "
        f"{picked} {by_service.get(picked, 0):,}, of {e1['summary']['total']:,} in otel_demo {ids['e1']}."
    )
    lines.append(
        f"- {picked}'s {e2['summary']['total']:,} error Log Entries peak at {peak_count:,} in the hour "
        f"{peak_bucket} {ids['e2']}."
    )
    lines.append(
        f"- {burst['count']:,} of them fall in the {burst_hours} hours from {burst_from.isoformat()} to "
        f"{burst_to.isoformat()}, against {burst['prev_period']:,} in the {burst_hours} hours before "
        f"(baseline_flag {burst['baseline_flag']}) {ids['e3']}."
    )
    lines.append(
        f"- The last of them in the burst that carries a trace_id: {report.entry['ts']} {picked} "
        f'"{report.entry["message"]}", trace_id {report.entry["trace_id"]}, span_id '
        f"{report.entry['span_id']} "
        "(selection query below)."
    )
    lines.append(
        f"- That trace has {summary['spans_total']} spans over {len(summary['services'])} services and lasts "
        f"{summary['trace_duration_ms']} ms; {summary['error_spans']} spans have status Error {ids['e4']}."
    )
    if origin is not None:
        lines.append(
            f'- The request failed deepest in {origin["service"]}: span "{origin["operation"]}" (depth '
            f"{origin['depth']}, no Error span below it) {ids['e4']}."
        )
    for row in failing[:2]:
        lines.append(f'- {row["service"]} span "{row["name"]}" reports "{row["text"]}" {ids["e4"]}.')
    if slowest is not None:
        lines.append(
            f'- Most self time: {slowest["service"]} "{slowest["operation"]}", '
            f"{slowest['self_ms']} ms of its "
            f"{slowest['duration_ms']} ms {ids['e4']}."
        )
    lines.append(
        f"- The trace has {summary['log_entries_total']} Log Entries, {summary['error_log_entries']} at an "
        f"error level {ids['e4']}."
    )
    window = summary["metrics_window"]
    if metrics and window:
        top = "; ".join(
            f"{row['service']} {row['name']} {_num(row['value'])}{_unit(row['unit'])} "
            f"against a median minute of {_num(row['baseline_median'])} ({row['status']})"
            for row in metrics[:4]
        )
        lines.append(
            f"- From {window['from']} to {window['to']}, {summary['metric_series_unusual']} of the "
            f"{summary['metric_series_total']} metric series of the trace's services were outside their "
            f"p05 to p95 range; the furthest: {top} {ids['e4']}."
        )
    jump = furthest_from_median(e4, picked)
    if jump is not None and jump not in metrics[:4] and window:
        lines.append(
            f"- {picked} {jump['name']} was {_num(jump['value'])}{_unit(jump['unit'])} in the window "
            f"against a median minute of {_num(jump['baseline_median'])} over the run, the largest change "
            f"against its median of the returned series ({jump['status']}"
            + (
                f": its p95 over the run is {_num(jump['baseline_p95'])}, so the state lasted long enough to "
                "be part of its own baseline"
                if jump["status"] == "normal"
                else ""
            )
            + f") {ids['e4']}."
        )
    flagged = [row["name"] for row in metrics if row["service"] == picked][:3]
    moved = (
        f"The unusual {_names(flagged)} of {picked} in the same minutes"
        if flagged
        else f"{picked}'s metrics in the same minutes, which stayed inside their usual range,"
    )
    lines += [
        "",
        "### Hypotheses",
        "",
        f'- {picked}\'s card cache filled up ("cache full: cannot add new item"), so its Charge calls fail '
        "and checkout cannot place the order. The trace, its Log Entries and the error burst are consistent "
        "with that; they do not show why the cache filled.",
        f"- {moved} are consistent with the service struggling while its cache is full; that is a "
        "correlation in time, not an established cause.",
        "",
        "### Unknowns",
        "",
        "- Why the cache filled, and why the burst starts and ends when it does: the logs, spans and metrics "
        "show the failure, not a configuration change or a deployment.",
        "- Whether every error Log Entry of the burst is this same failure: grouping them by message needs "
        "full-text search, which otel_demo does not offer without reading the whole Body column.",
        "",
        "### Next Step (Engineer)",
        "",
        f"- Check {picked}'s card cache: its size limit and eviction, and what changed at the start of the "
        f"burst (flagd, the feature-flag service, appears in otel_demo); then follow one trace from before "
        f"the burst with get_trace and compare the {picked} spans.",
    ]
    return lines


def report_id(evidence: dict[str, Any]) -> str:
    return f"[{evidence['evidence_id']}]"


def render(report: Report) -> str:
    e1, e2, e3, e4 = report.count_by, report.trend, report.burst, report.trace
    summary = e4["summary"]
    peak_bucket, peak_count = peak(e2)
    reads = report.trace_reads
    cold_ms = sum(read.plan.total_ms for read in reads)
    cold_bytes = sum(read.plan.http_in or 0 for read in reads)
    total_rows = sum(info.rows for info in report.files if info.signal.startswith("metrics"))
    logs = next(info for info in report.files if info.signal == "logs")
    traces = next(info for info in report.files if info.signal == "traces")
    lines = [
        "# Multimodal correlation: from an error Log Entry to its trace, span and metrics (T21)",
        "",
        "Generated by `make bench-multimodal` (`bench/multimodal.py`); do not edit by hand. The Evidence of "
        f"this run is in `{report.ledger_file}`, one JSON record per id.",
        "",
        "## Result",
        "",
        f"- **Data, queried in place:** otel_demo is one OpenTelemetry demo run as remote Parquet: "
        f"{logs.rows:,} Log Entries ({fmt_bytes(logs.size)}), {traces.rows:,} spans "
        f"({fmt_bytes(traces.size)}) "
        f"and {total_rows:,} metric data points, joined on trace_id. Only the metric roll-up is local "
        f"({report.roll_up_rows:,} rows over {report.roll_up_series:,} series).",
        f"- **How often** {report_id(e1)}: error Log Entries per service over the whole run, in "
        f"{fmt_ms(e1['query_ms'])}.",
        f"- **When** {report_id(e2)} {report_id(e3)}: {report.entry['service']}'s error Log Entries per hour "
        f"peak at {peak_count:,} in {peak_bucket} ({fmt_ms(e2['query_ms'])}); the burst around it holds "
        f"{row_dicts(e3)[0]['count']:,} against {row_dicts(e3)[0]['prev_period']:,} in as many hours before "
        f"({fmt_ms(e3['query_ms'])}).",
        f"- **One request:** the last {report.entry['service']} error Log Entry of the burst with "
        "a trace_id, "
        f'"{report.entry["message"]}" (trace {report.entry["trace_id"]}).',
    ]
    origin = summary["error_origin_spans"][0] if summary["error_origin_spans"] else None
    lines.append(
        f"- **The trace** {report_id(e4)}: {summary['spans_total']} spans over {len(summary['services'])} "
        f"services, {summary['error_spans']} with status Error"
        + (f'; it failed deepest in {origin["service"]} "{origin["operation"]}"' if origin else "")
        + f"; {summary['log_entries_total']} Log Entries; {summary['metric_series_unusual']} of "
        f"{summary['metric_series_total']} metric series of its services outside their usual range."
    )
    lines.append(
        f"- **Reads:** the first read of the trace took {fmt_ms(cold_ms)} of server time and fetched "
        f"{fmt_bytes(cold_bytes)} from S3, reading "
        + ", ".join(
            f"{read.plan.row_groups[0]} of {read.plan.row_groups[1]} row groups ({read.label})"
            for read in reads
            if read.plan.row_groups
        )
        + f"; get_trace right after took {fmt_ms(e4['query_ms'])} for all five statements. The TraceId bloom "
        "filters of the files are what keep a lookup in 12.9 GB of spans to a few row groups."
    )
    lines += ["", "## The investigation (Engineer)", ""]
    lines += [
        "Question: *payment errors keep appearing. What fails, where, and does anything else move with it?*",
        "",
        f"### Step 1: which services log errors, and how often {report_id(e1)}",
        "",
        f"`count_by(role='engineer', dataset='otel_demo', group_by='component', levels=['{LEVEL}'])`: "
        f"{fmt_ms(e1['query_ms'])}; it reads three narrow columns of the 4.3 GB file (no time slice needed).",
        "",
    ]
    lines += _table(
        ["Component", "Log Entries", "Share %"],
        [[row["component"] or "(none)", row["count"], row["share_pct"]] for row in row_dicts(e1)][:6],
        right=(1, 2),
    )
    lines += [
        "",
        f"### Step 2: when {report_id(e2)}",
        "",
        "`trend(role='engineer', dataset='otel_demo', bucket='hour', "
        f"components=['{report.entry['service']}'], "
        f"levels=['{LEVEL}'])`: {fmt_ms(e2['query_ms'])}. The hours with errors around the burst:",
        "",
    ]
    hours = [row for row in row_dicts(e2) if row["(all)"] >= peak_count / 100]
    lines += _table(
        ["Hour (UTC)", "Error Log Entries"], [[row["bucket"], row["(all)"]] for row in hours], right=(1,)
    )
    first_read, second_read = report.selection
    burst = row_dicts(e3)[0]
    scope = e3["data_scope"]
    lines += [
        "",
        "Every other hour has at most "
        f"{max((row['(all)'] for row in row_dicts(e2) if row['(all)'] < peak_count / 100), default=0):,}.",
        "",
        "`count_by(role='engineer', dataset='otel_demo', group_by='component', "
        f"components=['{burst['component']}'], "
        f"levels=['{LEVEL}'], time_from='{scope['time_from']}', time_to='{scope['time_to']}')` "
        f"{report_id(e3)}: "
        f"{burst['count']:,} error Log Entries in the burst, {burst['prev_period']:,} in the "
        "period of equal length "
        f"just before it, baseline_flag {burst['baseline_flag']}; {fmt_ms(e3['query_ms'])}.",
        "",
        "### Step 3: one error Log Entry with a trace_id",
        "",
        "The search step. search_logs (T5) is not merged on this branch, so the report runs its selection "
        "as SQL in two phases: row numbers from the narrow columns, then only those rows. otel_logs is not "
        "sorted by time, so a plain `LIMIT` over the wide columns would fetch many row groups.",
        "",
        f"1. {fmt_ms(first_read.plan.total_ms)}, {fmt_bytes(first_read.plan.http_in or 0)} from S3: "
        f"`{_short(first_read.statement)}`",
        f"2. {fmt_ms(second_read.plan.total_ms)}, {fmt_bytes(second_read.plan.http_in or 0)} from S3, "
        f"{second_read.plan.row_groups[0] if second_read.plan.row_groups else '?'} row group: "
        f"`{_short(second_read.statement)}`",
        "",
    ]
    lines += _table(
        ["ts", "Component", "Level", "trace_id", "span_id", "Message"],
        [[report.entry[k] for k in ("ts", "service", "level", "trace_id", "span_id", "message")]],
    )
    lines += [
        "",
        f"### Step 4: the trace across spans, Log Entries and metrics {report_id(e4)}",
        "",
        f"`get_trace(role='engineer', trace_id='{report.entry['trace_id']}')`: {fmt_ms(e4['query_ms'])} "
        f"(the bytes were cached by the measured first read). total_matches {e4['total_matches']:,}, "
        f"returned {e4['returned']}, truncated {str(e4['truncated']).lower()}.",
        "",
        "Spans, by start time (self_ms is the span's duration minus its child spans):",
        "",
    ]
    spans = [row for row in row_dicts(e4) if row["signal"] == "span"]
    lines += _table(
        [
            "Start",
            "Depth",
            "Service",
            "Operation",
            "Kind",
            "Status",
            "duration_ms",
            "self_ms",
            "Status message",
        ],
        [
            [
                row["ts"][11:],
                row["depth"],
                row["service"],
                row["name"],
                row["kind"],
                row["status"],
                row["duration_ms"],
                row["self_ms"],
                row["text"],
            ]
            for row in spans
        ],
        right=(1, 6, 7),
    )
    lines += ["", "Log Entries with this trace_id (span_id links each to its span):", ""]
    lines += _table(
        ["ts", "Component", "Level", "span_id", "Message"],
        [
            [row["ts"][11:], row["service"], row["status"], row["span_id"], row["text"]]
            for row in row_dicts(e4)
            if row["signal"] == "log"
        ],
    )
    window = summary["metrics_window"] or {}
    lines += [
        "",
        f"Metrics of the trace's services from {window.get('from')} to {window.get('to')}, most "
        "unusual first "
        f"({summary['metric_series_returned']} of {summary['metric_series_total']} series; "
        f"{summary['metric_series_unusual']} outside p05 to p95):",
        "",
    ]
    lines += _table(
        ["Service", "Metric", "Kind", "Value", "Unit", "p05", "Median minute", "p95", "Status"],
        [
            [
                row["service"],
                row["name"],
                row["kind"],
                row["value"],
                row["unit"],
                row["baseline_p05"],
                row["baseline_median"],
                row["baseline_p95"],
                row["status"],
            ]
            for row in row_dicts(e4)
            if row["signal"] == "metric"
        ][:12],
        right=(3, 5, 6, 7),
    )
    lines += ["", "## The answer", "", *answer(report)]
    lines += [
        "",
        "## Reads and cost",
        "",
        "Cost means two measured quantities, never money: bytes fetched from S3 (EXPLAIN ANALYZE HTTP "
        "statistics) and server time (EXPLAIN ANALYZE Total Time). Each remote statement below ran first "
        "with EXPLAIN ANALYZE (so on whatever the file cache held at that moment), then inside the tool.",
        "",
    ]
    lines += _table(
        ["Statement", "Row groups read", "Bytes from S3", "Server time"],
        [[read.label, *_plan_cells(read.plan)] for read in [*report.selection, *reads]],
        right=(1, 2, 3),
    )
    lines += [
        "",
        f"Tool times (query_ms, every statement of the call, each with one round trip): count_by "
        f"{fmt_ms(e1['query_ms'])}, trend {fmt_ms(e2['query_ms'])}, count_by over the burst "
        f"{fmt_ms(e3['query_ms'])}, get_trace {fmt_ms(e4['query_ms'])}. "
        f"Setup: {SETUP_MEASURED}.",
        "",
        "## Data",
        "",
    ]
    lines += _table(
        ["File", "Signal", "Rows", "Row groups", "Size (HTTP HEAD)"],
        [
            [
                info.url.rsplit("/", 1)[-1],
                SIGNAL_TITLES[info.signal],
                info.rows,
                info.row_groups,
                fmt_bytes(info.size),
            ]
            for info in report.files
        ],
        right=(2, 3),
    )
    lines += [
        "",
        "None of the files is sorted by time or by trace_id: every row group of the log file spans nearly "
        "the whole run. Two things keep reads small instead. The Log Entry view's ts is the "
        "second-resolution "
        "TimestampTime column (13 MB for the whole file, against 305 MB for Timestamp), so counts and trends "
        "read only narrow columns. And the log and trace files carry a TraceId bloom filter in every row "
        "group, so `trace_id = <id>` reads only the row groups that may hold the trace.",
        "",
        "## Method and limits",
        "",
        "- The answer's numbers come from the cited Evidence in the ledger file; the selection query of step "
        "3 is SQL, not a tool, until search_logs (T5) is merged.",
        "- The metric rows come from a per-minute roll-up that `make otel-setup` computes once: gauges and "
        "non-monotonic sums as the mean data point, monotonic sums as the increase per minute, histograms as "
        "the mean observation (the files' sums and histograms are all cumulative). Metrics without a service "
        "(cluster and node metrics) are left out, because no span names its node.",
        "- unusual_high and unusual_low compare the window's value with the same series' per-minute p05 and "
        "p95 over the whole 46-hour run. They describe how rare the value is, not what caused it.",
        "- SereneDB keeps remote file data in an in-memory cache, lost when it restarts, so the bytes and "
        "server times above depend on what earlier queries left in it. A cold get_trace reads the TraceId "
        "bloom filters (about 425 MiB for both files, once; `make otel-setup` warms them) and about 20 MB "
        "for every row group that holds the trace, at about 7 MB/s from this server. "
        f"{COLD_MEASURED}. So get_trace runs each remote statement with SLM_TRACE_TIMEOUT_S (default "
        "120 s) rather than the 15 s default.",
        "- The Log Entry row statement reads its row groups twice (12 of 240): SereneDB first reads the "
        "columns its ORDER BY needs, then the other columns of the rows it keeps.",
        *(f"- {note}" for note in report.notes),
        "",
        "## Environment",
        "",
        *(f"- **{key}:** {value}" for key, value in report.environment.items()),
        "",
    ]
    return "\n".join(lines)


# The database ---------------------------------------------------------------------------------------


def files_of(session: Session, settings: Settings) -> list[FileInfo]:
    found = []
    for signal, url in settings.otel_demo_files().items():
        ((rows, groups),) = session.rows(
            sql.SQL("SELECT num_rows, num_row_groups FROM parquet_file_metadata({})").format(sql.Literal(url))
        )
        found.append(FileInfo(signal, url, content_length(url), int(rows), int(groups)))
    return found


def mogrify(session: Session, statement: str, params: Sequence[Any]) -> str:
    """The statement with its parameters as literals, for EXPLAIN ANALYZE."""
    return psycopg.ClientCursor(session.conn).mogrify(statement, params)


def pick_entry(
    session: Session, logs_url: str, service: str, hour: datetime
) -> tuple[dict[str, Any], list[Read]]:
    first, source = selection_statements(logs_url, service, hour)
    first_plan = session.explain(first)
    numbers = [int(row[0]) for row in session.rows(first)]
    if not numbers:
        raise BenchError(f"no {LEVEL} Log Entry of {service} in {hour.isoformat()}")
    second = entry_statement(source, numbers)
    second_plan = session.explain(second)
    picked = max(
        (row for row in session.rows(second) if row[4]), key=lambda row: (row[1], row[0]), default=None
    )
    if picked is None:
        raise BenchError(f"none of the last {LEVEL} Log Entries of {service} in {hour} carries a trace_id")
    number, ts, component, level, trace_id, span_id, body = picked
    entry = {
        "row": number, "ts": ts.isoformat(), "service": component, "level": level,
        "trace_id": trace_id, "span_id": span_id, "message": str(body)[:240],
    }  # fmt: skip
    return entry, [
        Read("selection, phase 1: row numbers from narrow columns", first, first_plan),
        Read("selection, phase 2: the picked rows", second, second_plan),
    ]


def trace_reads(session: Session, settings: Settings, trace: str) -> list[Read]:
    rel = relation("otel_demo", settings=settings)
    statements: list[tuple[str, str, list[Any]]] = [
        ("get_trace: spans per service", trace_tool.services_statement(rel), [trace]),
        (
            "get_trace: spans with depth and self time",
            trace_tool.spans_statement(rel),
            [trace, trace_tool.DEFAULT_SPANS],
        ),
        ("get_trace: Log Entries per level", trace_tool.log_levels_statement(rel), [trace]),
        ("get_trace: Log Entries", trace_tool.logs_statement(rel), [trace, trace_tool.DEFAULT_LOGS]),
    ]
    reads = []
    for label, statement, params in statements:
        text = mogrify(session, statement, params)
        print(f"EXPLAIN ANALYZE {label} ...", flush=True)
        reads.append(Read(label, text, session.explain(text)))
    return reads


def environment(session: Session, settings: Settings, note: str) -> dict[str, str]:
    version = str(session.rows("SELECT version()")[0][0])
    values = dict(
        session.rows(
            "SELECT name, setting FROM pg_settings WHERE name IN ('threads', 'memory_limit', "
            "'enable_external_file_cache')"
        )
    )
    parts = conninfo_to_dict(settings.database_url)
    server = f"{parts.get('host', 'localhost')}:{parts.get('port', '5432')}"
    return {
        "Date (UTC)": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "SereneDB": version,
        "Server": server + (f" ({note})" if note else ""),
        "Server threads": str(values.get("threads", "?")),
        "Server memory_limit": str(values.get("memory_limit", "?")),
        "External file cache": str(values.get("enable_external_file_cache", "?")),
        "Schema": settings.slm_schema,
        "Files": settings.otel_demo_url,
        "SLM_TRACE_TIMEOUT_S": f"{settings.trace_timeout_s:g}",
        "Client": f"{platform.platform()}, Python {platform.python_version()}, psycopg {psycopg.__version__}",
    }


def run(args: argparse.Namespace) -> Report:
    ledger_file = Path(args.out).with_suffix(".ledger.jsonl")
    ledger_file.unlink(missing_ok=True)
    settings = load_settings(ledger_path=ledger_file)
    set_settings(settings)
    ledger.reset_ledger()
    session = Session(settings.database_url)
    facts = sql.Identifier(settings.slm_schema, "otel_facts")
    try:
        session.rows(sql.SQL("SELECT count(*) FROM {}").format(facts))
    except psycopg.Error as exc:
        raise BenchError(
            f"otel_demo is not set up in schema {settings.slm_schema} ({exc}); run `make otel-setup` first"
        ) from exc
    print("files ...", flush=True)
    files = files_of(session, settings)
    minute = sql.Identifier(settings.slm_schema, "otel_metric_minute")
    baseline = sql.Identifier(settings.slm_schema, "otel_metric_baseline")
    ((roll_up_rows, roll_up_series),) = session.rows(
        sql.SQL("SELECT (SELECT count(*) FROM {}), (SELECT count(*) FROM {})").format(minute, baseline)
    )
    tools = Tools()
    print("E1 count_by ...", flush=True)
    e1 = tools.call("count_by", role="engineer", group_by="component", dataset="otel_demo", levels=[LEVEL])
    service = args.service or next(row["component"] for row in row_dicts(e1) if row["component"] == "payment")
    print(f"E2 trend for {service} ...", flush=True)
    e2 = tools.call(
        "trend", role="engineer", dataset="otel_demo", bucket="hour", components=[service], levels=[LEVEL]
    )
    burst_from, burst_to = burst_window(e2)
    print(f"E3 count_by over the burst {burst_from} to {burst_to} ...", flush=True)
    e3 = tools.call(
        "count_by",
        role="engineer",
        group_by="component",
        dataset="otel_demo",
        components=[service],
        levels=[LEVEL],
        time_from=burst_from.isoformat(),
        time_to=burst_to.isoformat(),
    )
    hour = selection_hour(e3)
    print(f"the error Log Entry of {service} in {hour} ...", flush=True)
    entry, selection = pick_entry(session, settings.otel_demo_files()["logs"], service, hour)
    reads = trace_reads(session, settings, entry["trace_id"])
    print(f"E4 get_trace {entry['trace_id']} ...", flush=True)
    started = time.perf_counter()
    e4 = tools.call("get_trace", role="engineer", trace_id=entry["trace_id"])
    print(f"  {time.perf_counter() - started:.2f}s", flush=True)
    return Report(
        files=files,
        roll_up_rows=int(roll_up_rows),
        roll_up_series=int(roll_up_series),
        count_by=e1,
        trend=e2,
        burst=e3,
        entry=entry,
        selection=selection,
        trace=e4,
        trace_reads=reads,
        environment=environment(session, settings, args.server_note),
        ledger_file=str(ledger_file.relative_to(ROOT))
        if ledger_file.is_relative_to(ROOT)
        else str(ledger_file),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="the report to write")
    parser.add_argument("--service", help="the service to follow (default: payment)")
    parser.add_argument(
        "--server-note",
        default="shared dev instance reached through an SSH tunnel; it fetches the S3 files itself",
        help="a note on the server for the environment section",
    )
    parser.add_argument("--from-json", help="render the report from a saved run instead of measuring")
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.from_json:
        report = from_json(json.loads(Path(args.from_json).read_text()))
    else:
        try:
            report = run(args)
        except (BenchError, ConfigError, StopIteration) as exc:
            print(f"bench-multimodal: {exc}", file=sys.stderr)
            return 2
        out.with_suffix(".json").write_text(json.dumps(to_json(report), indent=1, default=str) + "\n")
    out.write_text(render(report))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
