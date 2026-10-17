#!/usr/bin/env python3
"""Huge datasets benchmark (T20): a billion remote Log Entries queried in place, and what it costs.

Usage (see `make help`):
    uv run python bench/huge_scale.py [--runs 3] [--whole-file-runs 1] [--out docs/results/huge-datasets.md]
        [--server-note TEXT]

What it does, against DATABASE_URL and SLM_SCHEMA (use a schema of its own, for example
SLM_SCHEMA=huge; `make bench-huge` does), on text_bench/part_000.parquet (SLM_TEXT_BENCH_URL):

    1. Data size: HTTP HEAD (Content-Length) of every text_bench file, from this machine.
    2. Layout: the Parquet footer of part_000 (rows, row groups, time range, column sizes), read by
       SereneDB without scanning any data.
    3. Sets the Dataset up when SLM_SCHEMA lacks it (`make huge-setup`; its facts pass reads the
       whole file once).
    4. Queries in place, no index (EXPLAIN ANALYZE, `--runs` each): the whole file once or more
       (`--whole-file-runs`), and a 60 s slice; each run reports the rows the Parquet scan produced,
       the row groups it read, the bytes SereneDB fetched over HTTP and the server time.
    5. The tools: count_by and trend over the 60 s slice through MCP, and the error an unbounded call
       gets.
    6. The agent index trade-off on an 8 s slice of about 4.5M Log Entries: a full-text scan on fresh
       slices (cold: the bytes come from S3), `create_index` (`--runs` builds, dropped in between),
       then the same full-text queries as a scan (warm: SereneDB's in-memory file cache holds the
       slice after the build) and through the agent index; break-even against both scans. The index
       is dropped at the end, also after an error.
    7. Pattern keys of that slice with the generic g10 mask chain (#6), distinct messages first.
    8. Writes the report (`--out`): results, every run, the cost definition and the environment.

Server times are EXPLAIN ANALYZE "Total Time"; bytes are its "HTTPFS HTTP Stats" (`in`), which
SereneDB 26.10.1 prints for remote reads, so "bytes scanned" is measured, not estimated. It never
indexes the whole file: one agent index of at most 5,000,000 Log Entries exists at a time.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import math
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import anyio
import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from index_tradeoff import BenchError, break_even
from serenedb_log_mcp.config import ConfigError, Settings, load_settings, set_settings
from serenedb_log_mcp.corpus import agent_indexes, facts
from serenedb_log_mcp.corpus.datasets import DATASETS, REMOTE_FACTS, relation
from serenedb_log_mcp.index_admin import existing_agent_indexes
from serenedb_log_mcp.server import create_server

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "results" / "huge-datasets.md"
DEFAULT_JSON = ROOT / "docs" / "results" / "huge-datasets.json"
FILE_COUNT = 50
"""The brief lists text_bench/part_000.parquet to part_049.parquet."""
PROBE_EXTRA = 2
"""HEAD this many files past the brief's list too: docs/decisions.md says 52 files."""

DAY = datetime(2025, 9, 23)
COLD_RUNS = 3
"""Fresh slices for cold scans: one per run, so each run fetches its bytes from S3."""


@dataclass(frozen=True)
class Slices:
    """Where the benchmark reads. Pick minutes no earlier run touched, so first reads are cold:
    SereneDB keeps remote file data in an in-memory cache until it restarts."""

    scan_minute: int = 24
    index_minute: int = 16

    @property
    def scan(self) -> tuple[datetime, datetime]:
        """60 s of text_bench, the longest slice a call may read without an index."""
        start = DAY + timedelta(minutes=self.scan_minute)
        return start, start + timedelta(seconds=60)

    @property
    def index(self) -> tuple[datetime, datetime]:
        """8 s of text_bench, about 4.8M Log Entries: the agent index slice."""
        start = DAY + timedelta(minutes=self.index_minute)
        return start, start + timedelta(seconds=8)

    @property
    def cold(self) -> list[tuple[datetime, datetime]]:
        """8 s slices in the next minutes, one per cold run."""
        return [
            (
                DAY + timedelta(minutes=self.index_minute + i),
                DAY + timedelta(minutes=self.index_minute + i, seconds=8),
            )
            for i in range(1, COLD_RUNS + 1)
        ]


TERMS = ("visa", "currency")
"""Words of the OTel demo's checkout failures ("Visa cache full", "failed to convert currency")."""
SHAPES = ("count", "by_component", "by_level", "first_rows")

STATEMENT_TIMEOUT_S = 900.0
RECONNECT_WAIT_S = 60.0
CONNECT_TIMEOUT_S = 10
RTT_SAMPLES = 11
SERVER_SETTINGS = ("threads", "memory_limit", "enable_external_file_cache", "http_timeout")

# The generic g10 mask chain of #6 (research/6-NoNaim95 in_db/mask_macros.sql), identical to
# ${slm}.pattern_mask_g10 in T9's sql/derive/20_patterns.sql; created in SLM_SCHEMA by this benchmark.
G10_MACRO = r"""CREATE OR REPLACE MACRO {name}(s) AS
    regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
    regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
        s,
        '[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}', '<*>', 'g'),
        '\d+\.\d+\.\d+\.\d+(:\d+)?', '<*>', 'g'),
        '\b([0-9A-Fa-f]{{2}}:){{2,}}[0-9A-Fa-f]{{2}}\b', '<*>', 'g'),
        '\b[\w-]+(\.[\w-]+)+\.(com|net|org|edu|us|local|io)\b', '<*>', 'g'),
        '(/[^\s/:,()\[\]\"'']+)+/?', '<*>', 'g'),
        '[^\s(),=:\[\].\"'']*\d[^\s(),=:\[\].\"'']*', '<*>', 'g'),
        '<\*>([.,:]<\*>)+', '<*>', 'g'),
        '(?i)\b(true|false|null|root|admin)\b', '<*>', 'g'),
        '\.*(<\*>)+', '<*>', 'g'),
        '<\*>([.,:]<\*>)+', '<*>', 'g'),
        '<\*>( <\*>)+', '<*>', 'g')"""

_T = TypeVar("_T")


# Pure parts -----------------------------------------------------------------------------------------

_UNITS = {
    "bytes": 1,
    "byte": 1,
    "b": 1,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
}
_SIZE_RE = re.compile(r"([0-9][0-9.,]*)\s*([A-Za-z]+)")


def parse_size(text: str) -> int:
    """A size as EXPLAIN ANALYZE prints it ('1.1 MiB', '536.7 MB', '0 bytes') in bytes."""
    match = _SIZE_RE.search(text)
    if match is None or match.group(2).lower() not in _UNITS:
        raise BenchError(f"not a size: {text!r}")
    return round(float(match.group(1).replace(",", "")) * _UNITS[match.group(2).lower()])


@dataclass(frozen=True)
class Plan:
    """What one EXPLAIN ANALYZE run reports."""

    total_ms: float
    rows_scanned: int | None = None
    """Rows the table scans produced (after Parquet row-group pruning and pushed-down filters)."""
    row_groups: tuple[int, int] | None = None
    """Row groups read of the file's total."""
    http_in: int | None = None
    """Bytes SereneDB fetched over HTTP for this statement (None: no remote read)."""
    http_gets: int | None = None
    data_read: int | None = None

    @property
    def cold(self) -> bool:
        """Did the bytes come over the network (rather than from SereneDB's file cache)?"""
        return bool(self.http_in)


_BOX = re.compile(r"[│┃║|]")


def parse_plan(text: str) -> Plan:
    """The numbers of an EXPLAIN ANALYZE plan (SereneDB 26.10.1 box format)."""
    total = re.search(r"Total Time:\s*([0-9.]+)s", text)
    if total is None:
        raise BenchError(f"EXPLAIN ANALYZE printed no 'Total Time': {text[:200]!r}")
    http_in = re.search(r"\bin:\s*([0-9.,]+\s*[A-Za-z]+)", text)
    gets = re.search(r"#GET:\s*([0-9,]+)", text)
    data_read = re.search(r"Data Read:\s*([0-9.,]+\s*[A-Za-z]+)", text)
    groups = [(int(a), int(b)) for a, b in re.findall(r"Row Groups Scanned:\s*([0-9]+)\s*/\s*([0-9]+)", text)]
    scanned = _scan_rows(text.splitlines())
    return Plan(
        total_ms=float(total.group(1)) * 1000,
        rows_scanned=sum(scanned) if scanned else None,
        row_groups=(sum(a for a, _ in groups), sum(b for _, b in groups)) if groups else None,
        http_in=parse_size(http_in.group(1)) if http_in else None,
        http_gets=int(gets.group(1).replace(",", "")) if gets else None,
        data_read=parse_size(data_read.group(1)) if data_read else None,
    )


def _scan_rows(lines: Sequence[str]) -> list[int]:
    """The output rows of every TABLE_SCAN box. Boxes can sit side by side on the same lines, so a
    scan's row count is the `N rows` line that starts in the column its own content starts in."""
    found: list[int] = []
    column: int | None = None
    for line in lines:
        if column is None:
            if (header := line.find("TABLE_SCAN")) >= 0:
                column = max(header - 1, 0)  # "╭─ TABLE_SCAN": the content starts under the dash
            continue
        for match in re.finditer(r"([0-9][0-9,]*) rows", line):
            if abs(match.start(1) - column) <= 1:
                found.append(int(match.group(1).replace(",", "")))
                column = None
                break
    return found


def file_urls(first: str, count: int) -> list[str]:
    """part_000 ... part_<count-1> next to `first` (a URL ending in part_000.parquet)."""
    match = re.search(r"part_(\d+)\.parquet$", first)
    if match is None:
        return [first]
    width, base = len(match.group(1)), first[: match.start()]
    return [f"{base}part_{i:0{width}d}.parquet" for i in range(count)]


Opener = Callable[..., Any]


def content_length(url: str, opener: Opener = urllib.request.urlopen) -> int | None:
    """The object size from an HTTP HEAD request; None when it does not exist (or is not HTTP)."""
    if not url.startswith(("http://", "https://")):
        return None
    request = urllib.request.Request(url, method="HEAD")
    try:
        with opener(request, timeout=20) as response:
            value = response.headers.get("Content-Length")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return None
        raise
    return int(value) if value is not None else None


def fmt_bytes(n: int | None) -> str:
    """Bytes as decimal units with the binary value next to it for MiB and above."""
    if n is None:
        return "n/a"
    if n == 0:
        return "0 B"
    for unit, size, binary, bsize in (
        ("TB", 1000**4, "TiB", 1024**4),
        ("GB", 1000**3, "GiB", 1024**3),
        ("MB", 1000**2, "MiB", 1024**2),
    ):
        if n >= size:
            return f"{n / size:,.2f} {unit} ({n / bsize:,.2f} {binary})"
    return f"{n / 1000:,.1f} kB" if n >= 1000 else f"{n} B"


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "n/a"
    return f"{ms / 1000:,.2f} s" if ms >= 1000 else f"{ms:,.1f} ms"


def fmt_int(n: int | None) -> str:
    return "n/a" if n is None else f"{n:,}"


def median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else float("nan")


# Measurements ---------------------------------------------------------------------------------------


@dataclass
class Measured:
    """One statement, run several times with EXPLAIN ANALYZE."""

    label: str
    statement: str
    runs: list[Plan] = field(default_factory=list)

    @property
    def median_ms(self) -> float:
        return median([run.total_ms for run in self.runs])

    @property
    def first(self) -> Plan:
        return self.runs[0]

    def runs_text(self) -> str:
        return "; ".join(
            f"{fmt_ms(r.total_ms)}, {fmt_bytes(r.http_in) if r.http_in is not None else 'no HTTP'} in"
            for r in self.runs
        )


@dataclass(frozen=True)
class ToolRun:
    tool: str
    call: str
    query_ms: list[float]
    wall_ms: list[float]
    summary: str
    routed: bool


@dataclass(frozen=True)
class Build:
    build_ms: float
    create_ms: float
    refresh_ms: float
    rows: int


@dataclass
class TradeOff:
    """Full-text queries over the agent index slice: cold scan, warm scan, agent index."""

    shape: str
    term: str
    warm: Measured
    agent: Measured
    scan_count: int | None = None
    agent_count: int | None = None


@dataclass
class Report:
    url: str
    env: dict[str, str]
    sizes: list[tuple[str, int | None]]
    layout: dict[str, Any]
    columns: list[tuple[str, int, int]]
    setup_s: float | None
    in_place: list[Measured]
    tools: list[ToolRun]
    guard: str
    cold_scans: Measured
    builds: list[Build]
    tradeoffs: list[TradeOff]
    routed_tool: ToolRun | None
    patterns: dict[str, Any]
    runs: int
    slices: Slices = field(default_factory=Slices)

    # The numbers the headline and the cost section use ------------------------------------------

    @property
    def build_cold(self) -> Build:
        """The first build: it reads the fresh slice from S3."""
        return self.builds[0]

    @property
    def build_warm(self) -> Build:
        """The median of the later builds, which find the slice in SereneDB's file cache."""
        later = self.builds[1:] or self.builds
        return sorted(later, key=lambda b: b.build_ms)[len(later) // 2]

    @property
    def cold_ms(self) -> float:
        return self.cold_scans.median_ms

    @property
    def warm_ms(self) -> float:
        return statistics.mean([t.warm.median_ms for t in self.tradeoffs])

    @property
    def agent_ms(self) -> float:
        return statistics.mean([t.agent.median_ms for t in self.tradeoffs])

    def break_even_fresh(self) -> int | None:
        """A fresh slice: cold build + n index queries against one cold scan + (n - 1) warm scans."""
        return fresh_break_even(self.build_cold.build_ms, self.cold_ms, self.warm_ms, self.agent_ms)

    def break_even_cold(self) -> int | None:
        """Every scan cold (the cache lost the slice, for example after a restart)."""
        return break_even(self.build_cold.build_ms, self.cold_ms - self.agent_ms)

    def break_even_warm(self) -> int | None:
        """The slice already cached: a warm build against warm scans."""
        return break_even(self.build_warm.build_ms, self.warm_ms - self.agent_ms)

    def markdown(self) -> str:
        return render(self)


# Rendering ------------------------------------------------------------------------------------------


def fresh_break_even(build_ms: float, cold_ms: float, warm_ms: float, agent_ms: float) -> int | None:
    """Smallest n with build + n * agent <= cold + (n - 1) * warm; None when the index never pays off."""
    saved = warm_ms - agent_ms
    if saved <= 0:
        return None
    needed = (build_ms - cold_ms + warm_ms) / saved
    return max(1, math.ceil(needed))


def _span(values: Iterator[float] | Sequence[float]) -> str:
    found = sorted(values)
    return fmt_ms(found[0]) if found[0] == found[-1] else f"{fmt_ms(found[0])} to {fmt_ms(found[-1])}"


def rows_covered(plan: Plan, file_rows: int | None) -> str:
    """The rows in the row groups a scan read: exact when it read all of them, else proportional (≈)."""
    if plan.row_groups is None or not file_rows:
        return fmt_int(plan.rows_scanned)
    read, total = plan.row_groups
    if read >= total:
        return fmt_int(file_rows)
    return f"≈{round(file_rows * read / total):,}"


def _covered_count(plan: Plan, file_rows: int | None) -> int:
    if plan.row_groups is None or not file_rows:
        return plan.rows_scanned or 0
    read, total = plan.row_groups
    return round(file_rows * min(read, total) / total)


def to_json(report: Report) -> dict[str, Any]:
    """Every measurement of a run, so the report can be rendered again (`--from-json`)."""
    raw = dataclasses.asdict(report)
    raw["layout"] = {
        k: (v.isoformat(sep=" ") if isinstance(v, datetime) else v) for k, v in report.layout.items()
    }
    return raw


def from_json(raw: dict[str, Any]) -> Report:
    def measured(m: dict[str, Any]) -> Measured:
        runs = [
            Plan(**{**r, "row_groups": tuple(r["row_groups"]) if r.get("row_groups") else None})
            for r in m["runs"]
        ]
        return Measured(m["label"], m["statement"], runs)

    def tool(t: dict[str, Any] | None) -> ToolRun | None:
        return None if t is None else ToolRun(**t)

    return Report(
        url=raw["url"],
        env=raw["env"],
        sizes=[(str(u), n) for u, n in raw["sizes"]],
        layout=raw["layout"],
        columns=[(str(n), int(c), int(u)) for n, c, u in raw["columns"]],
        setup_s=raw["setup_s"],
        in_place=[measured(m) for m in raw["in_place"]],
        tools=[ToolRun(**t) for t in raw["tools"]],
        guard=raw["guard"],
        cold_scans=measured(raw["cold_scans"]),
        builds=[Build(**b) for b in raw["builds"]],
        tradeoffs=[
            TradeOff(
                t["shape"],
                t["term"],
                measured(t["warm"]),
                measured(t["agent"]),
                t["scan_count"],
                t["agent_count"],
            )
            for t in raw["tradeoffs"]
        ],
        routed_tool=tool(raw["routed_tool"]),
        patterns=raw["patterns"],
        runs=raw["runs"],
        slices=Slices(**raw["slices"]),
    )


def _queries(n: int | None) -> str:
    return "never" if n is None else f"{n:,} queries"


def _plan_cells(m: Measured) -> list[str]:
    first = m.first
    groups = "n/a" if first.row_groups is None else f"{first.row_groups[0]:,} / {first.row_groups[1]:,}"
    return [
        fmt_int(first.rows_scanned),
        groups,
        fmt_bytes(first.http_in),
        fmt_ms(m.median_ms),
        m.runs_text(),
    ]


def headline_rows(report: Report) -> list[list[str]]:
    """The summary table: what was read, how much, how long (also used in the PR)."""
    rows: list[list[str]] = []
    layout = report.layout
    file_rows = layout.get("rows")
    rows.append(
        [
            "Footer only: row count, time range and column sizes of part_000 (client wall time)",
            fmt_int(file_rows),
            "footer",
            fmt_ms(layout.get("footer_ms")),
        ]
    )
    for m in report.in_place:
        rows.append(
            [m.label, rows_covered(m.first, file_rows), fmt_bytes(m.first.http_in), fmt_ms(m.median_ms)]
        )
    cold = report.cold_scans
    rows.append(
        [
            f"Full-text scan, 8 s slice, no index, cold (median of {len(cold.runs)} fresh slices)",
            rows_covered(cold.first, file_rows),
            fmt_bytes(int(median([r.http_in or 0 for r in cold.runs]))),
            fmt_ms(cold.median_ms),
        ]
    )
    rows.append(
        [
            "Same scan, warm (slice in SereneDB's file cache), mean of shapes",
            rows_covered(report.tradeoffs[0].warm.first, file_rows),
            fmt_bytes(report.tradeoffs[0].warm.first.http_in),
            fmt_ms(report.warm_ms),
        ]
    )
    rows.append(
        [
            "create_index over a fresh 8 s slice (first build, cold)",
            fmt_int(report.build_cold.rows),
            "the slice's columns",
            fmt_ms(report.build_cold.build_ms),
        ]
    )
    if len(report.builds) > 1:
        rows.append(
            [
                f"create_index again, slice cached (median of {len(report.builds) - 1})",
                fmt_int(report.build_warm.rows),
                "0 B",
                fmt_ms(report.build_warm.build_ms),
            ]
        )
    rows.append(
        [
            "Same full-text queries through the agent index, mean of shapes",
            "index",
            "0 B",
            fmt_ms(report.agent_ms),
        ]
    )
    pat = report.patterns
    if pat:
        rows.append(
            [
                f"g10 Pattern keys of the 8 s slice: {fmt_int(pat.get('keys'))} keys from "
                f"{fmt_int(pat.get('distinct'))} distinct messages",
                fmt_int(pat.get("entries")),
                "cached",
                fmt_ms(pat.get("ms")),
            ]
        )
    return rows


def render(report: Report) -> str:
    out: list[str] = []
    add = out.append
    total_bytes = sum(n for _, n in report.sizes if n)
    present = [(u, n) for u, n in report.sizes if n]
    layout = report.layout
    cold_build, warm_build = report.build_cold, report.build_warm
    slices = report.slices
    add("# Huge datasets: a billion remote Log Entries in place (T20)")
    add("")
    add("Generated by `make bench-huge` (`bench/huge_scale.py`); do not edit by hand.")
    add("")
    add("## Result")
    add("")
    add(
        f"- **Data:** `text_bench` is {len(present)} Parquet files with {fmt_bytes(total_bytes)} on S3 "
        f"(HTTP HEAD). We query `{report.url.rsplit('/', 1)[-1]}` in place: "
        f"{fmt_int(layout.get('rows'))} OTel Log Entries, {fmt_bytes(layout.get('bytes'))}, "
        f"{fmt_int(layout.get('row_groups'))} row groups, {layout.get('first')} to {layout.get('last')} UTC. "
        "Nothing is copied into SereneDB and the file is never indexed whole."
    )
    wholes = [m for m in report.in_place if m.label.startswith("Whole file")]
    whole = max(wholes, key=lambda m: m.first.http_in or 0) if wholes else None
    for m in wholes:
        passed = m.first.rows_scanned
        add(
            f"- **{m.label}:** all {fmt_int(layout.get('rows'))} rows in {fmt_ms(m.first.total_ms)} (first "
            f"run), fetching {fmt_bytes(m.first.http_in)} over HTTP"
            + (" (its columns were already in SereneDB's file cache)" if not m.first.http_in else "")
            + (
                f"; {fmt_int(passed)} rows passed the filter pushed into the scan"
                if passed is not None and passed != layout.get("rows")
                else ""
            )
            + ". Only the columns the query names are read."
        )
    add(
        f"- **Time slices:** the file is sorted by Timestamp, so a slice reads only the row groups it "
        f"overlaps; tools on `text_bench` require a slice of at most "
        f"{DATASETS['text_bench'].remote.max_scan_span.total_seconds():g} s"  # type: ignore[union-attr]
        " and refuse an unbounded call with a hint to slice or to call `create_index`."
    )
    add(
        f"- **Agent index trade-off** (8 s slice, {fmt_int(cold_build.rows)} Log Entries): the first build "
        f"takes {fmt_ms(cold_build.build_ms)} (it reads the fresh slice from S3), a rebuild of the cached "
        f"slice {fmt_ms(warm_build.build_ms)}. A full-text query costs {fmt_ms(report.cold_ms)} as a cold "
        f"scan (bytes from S3), {fmt_ms(report.warm_ms)} as a warm scan (bytes in SereneDB's file cache) "
        f"and {fmt_ms(report.agent_ms)} through the agent index (means over {len(report.tradeoffs)} queries)."
    )
    aggregations = [t for t in report.tradeoffs if t.shape != "first_rows"]
    listings = [t for t in report.tradeoffs if t.shape == "first_rows"]
    if aggregations and listings:
        agg_index = _span([t.agent.median_ms for t in aggregations])
        agg_scan = _span([t.warm.median_ms for t in aggregations])
        list_index = _span([t.agent.median_ms for t in listings])
        list_scan = _span([t.warm.median_ms for t in listings])
        add(
            f"- **Where the index pays:** counts and group-bys take {agg_index} through the index against "
            f"{agg_scan} as warm scans; the first 20 rows in time order take {list_index} against "
            f"{list_scan}, because the index must hand back every matching row to sort it."
        )
    add(
        f"- **Break-even:** on a fresh slice (first scan cold, later scans warm) the index repays its "
        f"build after {_queries(report.break_even_fresh())}; when every scan is cold (the cache lost the "
        f"slice, for example after a restart) after {_queries(report.break_even_cold())}; on a cached "
        f"slice after {_queries(report.break_even_warm())}."
    )
    add("")
    add("| What | Rows covered | Bytes from S3 | Server time |")
    add("|---|---:|---:|---:|")
    for row in headline_rows(report):
        add("| " + " | ".join(row) + " |")
    add("")
    add(
        "Rows covered are the rows in the Parquet row groups the scan read (EXPLAIN ANALYZE: all 1,624 for "
        "the whole file, ≈ in proportion for a slice); bytes are SereneDB's HTTP statistics for the first "
        "run; server time is the median EXPLAIN ANALYZE Total Time."
    )

    add("")
    add("## Data size (HTTP HEAD)")
    add("")
    add(
        f"Content-Length of every file the brief lists (part_000 to part_{FILE_COUNT - 1:03d}), plus "
        f"{PROBE_EXTRA} past it, requested from the benchmark client."
    )
    add("")
    missing = [u.rsplit("/", 1)[-1] for u, n in report.sizes if not n]
    sizes = [n for _, n in present]
    add(f"- Files found: {len(present)}; total {fmt_bytes(total_bytes)}.")
    if sizes:
        add(
            f"- Per file: smallest {fmt_bytes(min(sizes))}, median {fmt_bytes(int(median(sizes)))}, "
            f"largest {fmt_bytes(max(sizes))}."
        )
    if missing:
        add(f"- Not found: {', '.join(missing)}.")
    add(
        f"- part_000 holds {fmt_int(layout.get('rows'))} rows, so the {len(present)} files hold about "
        f"{fmt_int((layout.get('rows') or 0) * len(present))} rows if they are alike (not counted)."
    )

    add("")
    add("## Layout of part_000 (Parquet footer)")
    add("")
    add(f"- Writer: {layout.get('created_by')}.")
    add(
        f"- {fmt_int(layout.get('rows'))} rows in {fmt_int(layout.get('row_groups'))} row groups "
        f"(about {fmt_int(layout.get('rows_per_group'))} rows and {fmt_bytes(layout.get('bytes_per_group'))} "
        "per row group)."
    )
    add(
        f"- Timestamp from {layout.get('first')} to {layout.get('last')} UTC "
        f"({layout.get('seconds', 0):,.0f} s, about {fmt_int(layout.get('rows_per_second'))} Log Entries "
        f"per second); sorted by Timestamp: {layout.get('sorted')}."
    )
    add(f"- Footer read in {fmt_ms(layout.get('footer_ms'))} (wall, from the client).")
    add("")
    add("| Column | Compressed | Uncompressed |")
    add("|---|---:|---:|")
    for name, comp, uncomp in report.columns:
        add(f"| {name} | {fmt_bytes(comp)} | {fmt_bytes(uncomp)} |")

    add("")
    add("## Queries in place, without an index")
    add("")
    if report.setup_s is not None:
        add(
            f"`make huge-setup` ran first ({report.setup_s:,.1f} s wall): the view plus its facts, one pass "
            "over the whole file's ServiceName and SeverityText columns."
        )
        add("")
    add(
        "Each statement ran with EXPLAIN ANALYZE. `Rows covered` are the rows in the row groups the scan "
        "read (exact for the whole file, ≈ in proportion for a slice); `rows out of the scan` are those left "
        "after the filters SereneDB pushed into the Parquet reader. Row groups and bytes are from the first "
        "run; later runs usually read from SereneDB's in-memory file cache (0 bytes in)."
    )
    add("")
    add(
        "| Query | Rows covered | Rows out of the scan | Row groups | Bytes from S3 (first run) "
        "| Server time (median) | Every run |"
    )
    add("|---|---:|---:|---:|---:|---:|---|")
    for m in [*report.in_place, report.cold_scans]:
        add(
            f"| {m.label} | {rows_covered(m.first, layout.get('rows'))} | "
            + " | ".join(_plan_cells(m))
            + " |"
        )
    add("")
    add("The statements:")
    add("")
    for m in [*report.in_place, report.cold_scans]:
        add(f"- {m.label}: `{' '.join(m.statement.split())}`")

    add("")
    add("## Through the tools")
    add("")
    add(
        "count_by and trend run unchanged on `text_bench`: only the Dataset registry and the resolver "
        "know it is remote. `query_ms` is the Evidence's own timing (every statement, each with one "
        "network round trip, including the previous-period baseline)."
    )
    add("")
    add("| Tool | Call | query_ms (median) | Every run (query_ms) | Result |")
    add("|---|---|---:|---|---|")
    for tr in [*report.tools, *([report.routed_tool] if report.routed_tool else [])]:
        runs = ", ".join(fmt_ms(v) for v in tr.query_ms)
        add(f"| {tr.tool} | `{tr.call}` | {fmt_ms(median(tr.query_ms))} | {runs} | {tr.summary} |")
    add("")
    add("An unbounded call (`count_by(group_by='level', dataset='text_bench')`) is refused with:")
    add("")
    add(f"> {report.guard}")

    add("")
    add("## Agent index trade-off")
    add("")
    add(
        f"`create_index` over {slices.index[0].isoformat()} to {slices.index[1].isoformat()} "
        f"({fmt_int(cold_build.rows)} Log Entries): a partial inverted index over the view, all six columns, "
        "`message` with the `log_text` dictionary, refreshed with REINDEX INDEX. Builds (wall, from the "
        "tool's `build_ms`): "
        + ", ".join(
            f"{fmt_ms(b.build_ms)} (CREATE {fmt_ms(b.create_ms)} + REINDEX {fmt_ms(b.refresh_ms)})"
            for b in report.builds
        )
        + ". The first build reads the slice from S3; the later ones find it in the file cache."
    )
    add("")
    add(
        f"A cold scan is the first full-text query on a fresh 8 s slice ({len(report.cold_scans.runs)} "
        f"different slices, median {fmt_ms(report.cold_ms)}). A warm scan is the same query after the "
        "slice's columns are in SereneDB's file cache, which the build fills."
    )
    add("")
    add(
        "| Query | Shape | Matching (scan / index) | Warm scan (median) | Agent index (median) "
        "| Saved vs warm "
        "| Break-even, cached slice | Break-even, fresh slice |"
    )
    add("|---|---|---:|---:|---:|---:|---:|---:|")
    for t in report.tradeoffs:
        saved_warm = t.warm.median_ms - t.agent.median_ms
        fresh = fresh_break_even(cold_build.build_ms, report.cold_ms, t.warm.median_ms, t.agent.median_ms)
        add(
            f"| {t.term} | {t.shape} | {fmt_int(t.scan_count)} / {fmt_int(t.agent_count)} | "
            f"{fmt_ms(t.warm.median_ms)} | {fmt_ms(t.agent.median_ms)} | {fmt_ms(saved_warm)} | "
            f"{_queries(break_even(warm_build.build_ms, saved_warm))} | "
            f"{_queries(fresh)} |"
        )
    add("")
    add(
        "Matching counts can differ between the scan (a case-insensitive whole-word regex) and the index "
        "(the `log_text` tokenizer), because the two split words differently inside JSON bodies; the "
        "times compare the same work either way."
    )
    add("")
    add("Every run, server time:")
    add("")
    for t in report.tradeoffs:
        add(
            f"- {t.term} / {t.shape}: warm scan {', '.join(fmt_ms(r.total_ms) for r in t.warm.runs)}; "
            f"agent index {', '.join(fmt_ms(r.total_ms) for r in t.agent.runs)}"
        )
    add(f"- cold scans ({len(report.cold_scans.runs)} fresh slices): {report.cold_scans.runs_text()}")

    pat = report.patterns
    if pat:
        add("")
        add("## Pattern keys of one slice (g10)")
        add("")
        add(
            "The generic g10 mask chain (#6; T9's `pattern_mask_g10`) over the "
            f"{fmt_int(pat.get('entries'))} "
            f"Log Entries of the 8 s slice, distinct messages first: {fmt_int(pat.get('distinct'))} distinct "
            f"messages give {fmt_int(pat.get('keys'))} Pattern keys, in {fmt_ms(pat.get('ms'))} (wall). "
            "The top keys by Log Entries:"
        )
        add("")
        add("| Log Entries | Distinct messages | Pattern key (template text, first 120 characters) |")
        add("|---:|---:|---|")
        for key, entries, messages in pat.get("top", []):
            text = " ".join(str(key).split())[:120].replace("|", "\\|")
            add(f"| {entries:,} | {messages:,} | `{text}` |")

    add("")
    add("## Cost")
    add("")
    add(
        "Cost here means two measured quantities, never money: **bytes transferred** from S3 into "
        "SereneDB (EXPLAIN ANALYZE HTTP statistics) and **server time** (EXPLAIN ANALYZE Total Time) on "
        f"the hardware below ({report.env.get('Server threads', '?')} threads). Thread-seconds are server "
        "time times threads, an upper bound on the compute used. No price is quoted: what a byte or a "
        "second costs depends on where SereneDB runs and on the S3 region, and no public price was "
        "checked for this report."
    )
    add("")
    threads = _threads(report.env)
    add("| Query | Bytes from S3 | Server time | Thread-seconds (upper bound) | Rows covered per second |")
    add("|---|---:|---:|---:|---:|")
    for m in [*report.in_place, report.cold_scans]:
        first = m.first
        if not first.http_in:
            continue  # served from the cache: no transfer to price
        seconds = first.total_ms / 1000
        covered = _covered_count(first, layout.get("rows"))
        rate = covered / seconds if seconds else 0
        add(
            f"| {m.label} (first run) | {fmt_bytes(first.http_in)} | {fmt_ms(first.total_ms)} | "
            f"{seconds * threads:,.1f} | {rate:,.0f} |"
        )
    add(
        "| create_index, fresh 8 s slice (first build) | the slice's columns | "
        f"{fmt_ms(cold_build.build_ms)} | {cold_build.build_ms / 1000 * threads:,.1f} | "
        f"{cold_build.rows / (cold_build.build_ms / 1000):,.0f} |"
    )
    if whole is not None and present:
        files = len(present)
        add("")
        add(
            f"Extrapolation, labelled as such: the same whole-file aggregate over all {files} files would "
            f"fetch about {fmt_bytes((whole.first.http_in or 0) * files)} and take about "
            f"{whole.first.total_ms / 1000 * files / 60:,.0f} minutes at this server's measured rate, if "
            "the files are alike and read one after another. Time slices and agent indexes are how the "
            "tools avoid paying that."
        )

    add("")
    add("## Environment")
    add("")
    for key, value in report.env.items():
        add(f"- **{key}:** {value}")

    add("")
    add("## Method and limits")
    add("")
    add(
        "- EXPLAIN ANALYZE on SereneDB 26.10.1 prints HTTP statistics (bytes in, GET and HEAD requests) "
        'and "Data Read" for remote Parquet, so bytes are measured per statement. docs/decisions.md '
        "said it reports rows and time but not bytes; that no longer holds for remote reads."
    )
    add(
        "- SereneDB caches remote file data in memory (`enable_external_file_cache`). A first read "
        "fetches from S3 (cold); a repeat finds the bytes in the cache (warm, 0 bytes in). The cache is "
        "lost when the server restarts (it restarted after crashes during this work), while an agent "
        "index persists, so the cold column is what an agent pays on a fresh slice or after a restart."
    )
    add(
        "- The shared dev server is reached through an SSH tunnel and fetches the file from S3 itself; "
        "other agents used it at the same time. Server times exclude the tunnel; tool times include it."
    )
    add(
        "- The text_bench view casts the OTel Timestamp to TIMESTAMP, which keeps Parquet row-group "
        "pruning (timezone('UTC', ...) defeats it: a 1 s slice scanned all row groups) and depends on the "
        "session time zone being UTC, which `make huge-setup` checks."
    )
    add(
        "- An index over a view of remote files is a snapshot taken at CREATE INDEX time; REINDEX INDEX "
        "refreshes it. The file is a published benchmark file, so the snapshot matches it."
    )
    add(f"- Runs per measurement: {report.runs}; medians are reported, every run is listed.")
    add("")
    return "\n".join(out)


def _threads(env: dict[str, str]) -> int:
    try:
        return int(env.get("Server threads", "1"))
    except ValueError:
        return 1


# The database ---------------------------------------------------------------------------------------


class Session:
    """One autocommit connection with a cancel timeout per statement and one reconnect after a restart."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn = self._connect()

    def _connect(self) -> psycopg.Connection[tuple[Any, ...]]:
        return psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S, prepare_threshold=None
        )

    def rows(
        self, statement: str | sql.Composable, timeout_s: float = STATEMENT_TIMEOUT_S
    ) -> list[tuple[Any, ...]]:
        text = statement if isinstance(statement, str) else statement.as_string(None)
        return self._retry(lambda: self._timed(text, timeout_s))

    def explain(self, statement: str, timeout_s: float = STATEMENT_TIMEOUT_S) -> Plan:
        plan = self.rows("EXPLAIN ANALYZE " + statement, timeout_s)
        return parse_plan("\n".join(str(row[-1]) for row in plan))

    def _timed(self, text: str, timeout_s: float) -> list[tuple[Any, ...]]:
        timer = threading.Timer(timeout_s, self.conn.cancel_safe)
        timer.daemon = True
        timer.start()
        try:
            cursor = self.conn.execute(text)
            return cursor.fetchall() if cursor.description else []
        finally:
            timer.cancel()

    def _retry(self, work: Callable[[], _T]) -> _T:
        try:
            return work()
        except psycopg.OperationalError:
            if not (self.conn.closed or self.conn.broken):
                raise
        deadline = time.monotonic() + RECONNECT_WAIT_S
        while True:
            try:
                self.conn = self._connect()
                self.conn.execute("SELECT 1").fetchall()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        print("  (SereneDB restarted; retried the statement once)", file=sys.stderr)
        return work()


class Tools:
    """The product's MCP tools, called in process the way a client calls them."""

    def __init__(self) -> None:
        self.server = create_server()

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = anyio.run(lambda: self.server.call_tool(tool, arguments))
        if not isinstance(result, CallToolResult) or result.structured_content is None:
            raise BenchError(f"{tool} returned no Evidence: {result!r}")
        return dict(result.structured_content)

    def error(self, tool: str, **arguments: Any) -> str:
        try:
            result = anyio.run(lambda: self.server.call_tool(tool, arguments))
        except Exception as exc:  # the SDK raises the ToolError of an in-process call
            return str(exc).removeprefix(f"Error executing tool {tool}: ")
        raise BenchError(f"{tool} did not refuse {arguments}: {result!r}")


# Steps ----------------------------------------------------------------------------------------------


def _lit(value: Any) -> str:
    return sql.Literal(value).as_string(None)


def file_sizes(url: str) -> list[tuple[str, int | None]]:
    urls = file_urls(url, FILE_COUNT + PROBE_EXTRA)
    print(f"HEAD {len(urls)} files ...", flush=True)
    return [(u, content_length(u)) for u in urls]


def layout_of(session: Session, url: str) -> tuple[dict[str, Any], list[tuple[str, int, int]]]:
    started = time.perf_counter()
    (name, created_by, rows, groups, size) = session.rows(
        f"SELECT file_name, created_by, num_rows, num_row_groups, file_size_bytes "
        f"FROM parquet_file_metadata({_lit(url)})"
    )[0]
    first, last, ordered = session.rows(
        "WITH g AS (SELECT row_group_id, CAST(stats_min_value AS TIMESTAMPTZ) AS lo, "
        "CAST(stats_max_value AS TIMESTAMPTZ) AS hi FROM parquet_metadata("
        + _lit(url)
        + ") WHERE lower(path_in_schema) = 'timestamp') "
        "SELECT CAST(min(lo) AS TIMESTAMP), CAST(max(hi) AS TIMESTAMP), "
        "bool_and(prev_hi IS NULL OR lo >= prev_hi) FROM ("
        "SELECT lo, hi, lag(hi) OVER (ORDER BY row_group_id) AS prev_hi FROM g) AS s"
    )[0]
    footer_ms = (time.perf_counter() - started) * 1000
    columns = session.rows(
        "SELECT path_in_schema, CAST(sum(total_compressed_size) AS BIGINT), "
        "CAST(sum(total_uncompressed_size) AS BIGINT) FROM parquet_metadata("
        + _lit(url)
        + ") GROUP BY 1 ORDER BY 2 DESC"
    )
    seconds = (last - first).total_seconds() if first and last else 0.0
    layout = {
        "file": name,
        "created_by": created_by,
        "rows": int(rows),
        "row_groups": int(groups),
        "bytes": int(size),
        "rows_per_group": int(rows) // max(int(groups), 1),
        "bytes_per_group": int(size) // max(int(groups), 1),
        "first": first,
        "last": last,
        "seconds": seconds,
        "rows_per_second": int(int(rows) / seconds) if seconds else None,
        "sorted": "yes" if ordered else "no",
        "footer_ms": footer_ms,
    }
    return layout, [(str(n), int(c), int(u)) for n, c, u in columns]


def ensure_setup(session: Session, settings: Settings) -> float | None:
    """Run `make huge-setup` when the schema lacks the view or its facts; its wall seconds, or None."""
    found = session.rows(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = "
        + _lit(settings.slm_schema)
        + f" AND table_name IN ('tb_entry', {_lit(REMOTE_FACTS)})"
    )[0][0]
    if found == 2:
        return None
    print(f"text_bench is not set up in {settings.slm_schema}; running huge-setup ...", flush=True)
    started = time.perf_counter()
    script = ROOT / "scripts" / "huge_setup.py"
    command = [sys.executable, str(script), "--schema", settings.slm_schema, "--url", settings.text_bench_url]
    if subprocess.run(command, check=False).returncode != 0:
        raise BenchError("huge-setup failed; see its output above")
    return time.perf_counter() - started


def measure(session: Session, label: str, statement: str, runs: int) -> Measured:
    m = Measured(label, statement)
    for i in range(runs):
        plan = session.explain(statement)
        m.runs.append(plan)
        print(f"  {label} run {i + 1}: {fmt_ms(plan.total_ms)}, {fmt_bytes(plan.http_in)} in", flush=True)
    return m


def _where(start: datetime, end: datetime) -> str:
    return f"ts >= {_lit(start)} AND ts < {_lit(end)}"


def slice_queries(session: Session, table: str, slices: Slices, runs: int) -> list[Measured]:
    scan = _where(*slices.scan)
    return [
        measure(session, "60 s slice: count", f"SELECT count(*) FROM {table} WHERE {scan}", runs),
        measure(
            session,
            "60 s slice: Log Entries per Component and level",
            f"SELECT component, level, count(*) AS n FROM {table} WHERE {scan} GROUP BY component, level",
            runs,
        ),
        measure(
            session,
            "60 s slice: distinct traces of error Log Entries per Component",
            f"SELECT component, count(*) AS n, count(DISTINCT trace_id) AS traces FROM {table} "
            f"WHERE {scan} AND level = 'error' GROUP BY component",
            runs,
        ),
    ]


def whole_file_queries(session: Session, table: str, url: str, runs: int) -> list[Measured]:
    """Run last: they put whole columns into the file cache, which would warm every later slice.

    The first two read the Dataset's view, whose columns earlier steps (the setup's facts pass) may
    have cached; the third reads a narrow column no other step reads straight from the file, so its
    first run shows what a cold whole-file scan costs.
    """
    if runs <= 0:
        return []
    return [
        measure(
            session,
            "Whole file: Log Entries per Component and level",
            f"SELECT component, level, count(*) AS n FROM {table} GROUP BY component, level",
            runs,
        ),
        measure(
            session,
            "Whole file: error Log Entries per minute",
            f"SELECT time_bucket(INTERVAL '1 minute', ts) AS minute, count(*) AS n FROM {table} "
            "WHERE level = 'error' GROUP BY 1 ORDER BY 1",
            runs,
        ),
        measure(
            session,
            "Whole file, straight from the Parquet file: Log Entries per instrumentation scope",
            f'SELECT "ScopeName", count(*) AS n FROM read_parquet({_lit(url)}) GROUP BY 1',
            runs,
        ),
    ]


def _match(mode: str, term: str) -> str:
    if mode == "scan":
        return f"regexp_matches(e.message, {_lit(f'(?i)(^|[^a-z0-9]){term}([^a-z0-9]|$)')})"
    return f"e.message @@ websearch_to_tsquery({_lit(term)})"


def text_query(source: str, mode: str, term: str, shape: str, start: datetime, end: datetime) -> str:
    where = f"{_match(mode, term)} AND e.ts >= {_lit(start)} AND e.ts < {_lit(end)}"
    return {
        "count": f"SELECT count(*) FROM {source} AS e WHERE {where}",
        "by_component": f"SELECT e.component, count(*) AS n FROM {source} AS e WHERE {where} "
        "GROUP BY 1 ORDER BY n DESC, 1 LIMIT 10",
        "by_level": f"SELECT e.level, count(*) AS n FROM {source} AS e WHERE {where} "
        "GROUP BY 1 ORDER BY n DESC, 1",
        "first_rows": f"SELECT e.ts, e.component, e.trace_id, left(e.message, 200) FROM {source} AS e "
        f"WHERE {where} ORDER BY e.ts LIMIT 20",
    }[shape]


def _summary_of(evidence: dict[str, Any]) -> str:
    summary = evidence.get("summary", {})
    if "total" in summary:
        return f"total {summary['total']:,} Log Entries in {summary.get('groups_total', 0):,} groups"
    return str(summary)[:80]


def time_tool(tools: Tools, tool: str, call: str, runs: int, **arguments: Any) -> ToolRun:
    query_ms, wall_ms, last = [], [], {}
    for _ in range(runs):
        started = time.perf_counter()
        last = tools.call(tool, **arguments)
        wall_ms.append((time.perf_counter() - started) * 1000)
        query_ms.append(float(last["query_ms"]))
    print(f"  {tool} {call}: {fmt_ms(median(query_ms))}", flush=True)
    routed = bool(last.get("data_scope", {}).get("agent_indexes"))
    return ToolRun(tool, call, query_ms, wall_ms, _summary_of(last), routed)


@contextlib.contextmanager
def agent_index_dropped_after(tools: Tools) -> Iterator[list[str]]:
    """Track agent indexes the benchmark builds and drop them at the end, also after an error."""
    built: list[str] = []
    try:
        yield built
    finally:
        for name in built:
            with contextlib.suppress(Exception):
                tools.call("drop_index", role="engineer", name=name)


def pattern_keys(session: Session, schema: str, table: str, slices: Slices) -> dict[str, Any]:
    macro = sql.Identifier(schema, "pattern_mask_g10").as_string(None)
    session.rows(G10_MACRO.format(name=macro))
    where = _where(*slices.index)
    started = time.perf_counter()
    rows = session.rows(
        f"WITH d AS (SELECT message, count(*) AS n FROM {table} WHERE {where} GROUP BY message), "
        f"k AS (SELECT {macro}(coalesce(message, '')) AS key, sum(n) AS entries, count(*) AS messages "
        "FROM d GROUP BY 1) "
        "SELECT key, CAST(entries AS BIGINT), CAST(messages AS BIGINT), "
        "CAST(sum(entries) OVER () AS BIGINT), CAST(sum(messages) OVER () AS BIGINT), count(*) OVER () "
        "FROM k ORDER BY entries DESC LIMIT 10"
    )
    ms = (time.perf_counter() - started) * 1000
    if not rows:
        return {}
    return {
        "ms": ms,
        "entries": int(rows[0][3]),
        "distinct": int(rows[0][4]),
        "keys": int(rows[0][5]),
        "top": [(str(r[0]), int(r[1]), int(r[2])) for r in rows],
    }


def environment(session: Session, settings: Settings, runs: int, note: str) -> dict[str, str]:
    version = str(session.rows("SELECT version()")[0][0])
    values = dict(
        session.rows(
            "SELECT name, setting FROM pg_settings WHERE name IN ("
            + ", ".join(_lit(name) for name in SERVER_SETTINGS)
            + ")"
        )
    )
    samples = []
    for _ in range(RTT_SAMPLES):
        started = time.perf_counter()
        session.rows("SELECT 1")
        samples.append((time.perf_counter() - started) * 1000)
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
        "File": settings.text_bench_url,
        f"Round trip (SELECT 1), median of {RTT_SAMPLES}": fmt_ms(median(samples)),
        "Client": f"{platform.platform()}, Python {platform.python_version()}, psycopg {psycopg.__version__}",
        "Runs per measurement": str(runs),
    }


def run(args: argparse.Namespace) -> Report:
    settings = load_settings(ledger_path=None)
    set_settings(settings)
    facts.clear_cache()
    agent_indexes.clear()
    session = Session(settings.database_url)
    rel = relation("text_bench", settings=settings)
    table = rel.table.as_string(None)
    env = environment(session, settings, args.runs, args.server_note)
    sizes = file_sizes(settings.text_bench_url)
    layout, columns = layout_of(session, settings.text_bench_url)
    setup_s = ensure_setup(session, settings)
    if existing := [i for i in existing_agent_indexes(settings=settings) if i.dataset == "text_bench"]:
        raise BenchError(
            f"text_bench already has the agent index {existing[0].qualified_name}; this benchmark never "
            "touches an index it did not build. Drop it first (drop_index) or use another SLM_SCHEMA."
        )

    slices = Slices(args.scan_minute, args.index_minute)
    print("slice queries in place ...", flush=True)
    in_slices = slice_queries(session, table, slices, args.runs)

    print("tools ...", flush=True)
    tools = Tools()
    scan_scope = {"time_from": slices.scan[0].isoformat(), "time_to": slices.scan[1].isoformat()}
    tool_runs = [
        time_tool(
            tools,
            "count_by",
            "group_by='component', 60 s slice",
            args.runs,
            role="team_lead",
            group_by="component",
            dataset="text_bench",
            **scan_scope,
        ),
        time_tool(
            tools,
            "trend",
            "bucket='minute', group_by='level', 60 s slice",
            args.runs,
            role="team_lead",
            dataset="text_bench",
            bucket="minute",
            group_by="level",
            **scan_scope,
        ),
    ]
    guard = tools.error("count_by", role="manager", group_by="level", dataset="text_bench")

    print("agent index trade-off ...", flush=True)
    cold = Measured(
        "Full-text scan, 8 s slice, no index, cold",
        text_query(table, "scan", TERMS[0], "count", *slices.cold[0]),
    )
    for start, end in slices.cold[: args.runs]:
        plan = session.explain(text_query(table, "scan", TERMS[0], "count", start, end))
        cold.runs.append(plan)
        print(
            f"  cold scan {start.time()}: {fmt_ms(plan.total_ms)}, {fmt_bytes(plan.http_in)} in", flush=True
        )
    index_scope = {
        "dataset": "text_bench",
        "time_from": slices.index[0].isoformat(),
        "time_to": slices.index[1].isoformat(),
    }
    builds: list[Build] = []
    tradeoffs: list[TradeOff] = []
    routed: ToolRun | None = None
    with agent_index_dropped_after(tools) as built:
        for i in range(args.runs):
            evidence = tools.call("create_index", role="engineer", **index_scope)
            s = evidence["summary"]
            built[:] = [s["index"]]
            builds.append(
                Build(float(s["build_ms"]), float(s["create_ms"]), float(s["refresh_ms"]), int(s["rows"]))
            )
            print(f"  build {i + 1}: {fmt_ms(builds[-1].build_ms)} for {builds[-1].rows:,} rows", flush=True)
            if i < args.runs - 1:
                tools.call("drop_index", role="engineer", name=s["index"])
                built.clear()
        agent_rel = relation("text_bench", scope=_Scope(*slices.index), settings=settings)
        if agent_rel.agent_index is None:
            raise BenchError("the resolver did not route the slice to the agent index")
        index_source = agent_rel.index.as_string(None)
        for term in TERMS:
            for shape in SHAPES:
                warm = Measured(
                    f"{term} / {shape} scan", text_query(table, "scan", term, shape, *slices.index)
                )
                agent = Measured(
                    f"{term} / {shape} agent", text_query(index_source, "index", term, shape, *slices.index)
                )
                for _ in range(args.runs):
                    warm.runs.append(session.explain(warm.statement))
                    agent.runs.append(session.explain(agent.statement))
                t = TradeOff(shape, term, warm, agent)
                if shape == "count":
                    t.scan_count = int(session.rows(warm.statement)[0][0])
                    t.agent_count = int(session.rows(agent.statement)[0][0])
                print(
                    f"  {term}/{shape}: warm {fmt_ms(warm.median_ms)}, agent {fmt_ms(agent.median_ms)}",
                    flush=True,
                )
                tradeoffs.append(t)
        routed = time_tool(
            tools,
            "count_by",
            f"group_by='component', query='{TERMS[0]}', inside the agent index",
            args.runs,
            role="engineer",
            group_by="component",
            query=TERMS[0],
            **index_scope,
        )
        if not routed.routed:
            raise BenchError("count_by with a query inside the slice did not read the agent index")
        print("pattern keys ...", flush=True)
        patterns = pattern_keys(session, settings.slm_schema, table, slices)
    print("whole file ...", flush=True)
    whole = whole_file_queries(session, table, settings.text_bench_url, args.whole_file_runs)
    return Report(
        url=settings.text_bench_url,
        env=env,
        sizes=sizes,
        layout=layout,
        columns=columns,
        setup_s=setup_s,
        in_place=[*whole, *in_slices],
        tools=tool_runs,
        guard=guard,
        cold_scans=cold,
        builds=builds,
        tradeoffs=tradeoffs,
        routed_tool=routed,
        patterns=patterns,
        runs=args.runs,
        slices=slices,
    )


@dataclass(frozen=True)
class _Scope:
    start: datetime
    end: datetime

    @property
    def time_from(self) -> str:
        return self.start.isoformat()

    @property
    def time_to(self) -> str:
        return self.end.isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Huge datasets benchmark (T20).")
    parser.add_argument("--runs", type=int, default=3, help="runs per measurement (default 3, medians)")
    parser.add_argument(
        "--whole-file-runs",
        type=int,
        default=1,
        help="runs of the whole-file aggregate (default 1; 0 skips it)",
    )
    parser.add_argument(
        "--scan-minute", type=int, default=Slices.scan_minute, help="minute of the 60 s slice (0-28)"
    )
    parser.add_argument(
        "--index-minute",
        type=int,
        default=Slices.index_minute,
        help="minute of the 8 s agent index slice; cold scans read the next minutes (0-26)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report path")
    parser.add_argument(
        "--json",
        type=Path,
        default=DEFAULT_JSON,
        help="every measurement, for --from-json (default next to --out)",
    )
    parser.add_argument("--from-json", type=Path, help="render the report from a saved run; measure nothing")
    parser.add_argument("--server-note", default="", help="a note on the server for the environment section")
    args = parser.parse_args(argv)
    if args.from_json is not None:
        report = from_json(json.loads(args.from_json.read_text()))
        args.out.write_text(report.markdown())
        print(f"rendered {args.from_json} into {args.out}")
        return 0
    if not 1 <= args.runs <= COLD_RUNS:
        parser.error(f"--runs must be between 1 and {COLD_RUNS} (one fresh slice per cold run)")
    if not 0 <= args.scan_minute <= 28 or not 0 <= args.index_minute <= 29 - COLD_RUNS:
        parser.error(
            f"--scan-minute must be 0-28 and --index-minute 0-{29 - COLD_RUNS} (part_000 spans 29 minutes)"
        )
    if args.whole_file_runs < 0:
        parser.error("--whole-file-runs must be 0 or more")
    try:
        report = run(args)
    except (BenchError, ConfigError, ToolError, psycopg.Error) as exc:
        print(f"bench-huge: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(to_json(report), indent=1, default=str) + "\n")
    args.out.write_text(report.markdown())
    print(
        f"build cold {fmt_ms(report.build_cold.build_ms)} / cached {fmt_ms(report.build_warm.build_ms)}; "
        f"full-text query cold {fmt_ms(report.cold_ms)} / warm {fmt_ms(report.warm_ms)} / agent index "
        f"{fmt_ms(report.agent_ms)}; break-even fresh {_queries(report.break_even_fresh())}, always cold "
        f"{_queries(report.break_even_cold())}, cached {_queries(report.break_even_warm())}; wrote {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
