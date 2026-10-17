#!/usr/bin/env python3
"""Agent-managed index benchmark (T19): build time against query time saved, and the break-even.

Usage (see `make help`):
    uv run python bench/index_tradeoff.py [--dataset bgl] [--time-from 2005-08-22]
        [--time-to 2005-09-19] [--runs 3] [--out docs/results/index-tradeoff.md] [--server-note TEXT]

What it does, against DATABASE_URL and SLM_SCHEMA:
    1. Refuses to start when the Dataset already has an agent index (it never touches one it did
       not build), counts the slice and measures the network round trip.
    2. Times count_by and trend through the MCP tools with no agent index.
    3. Builds the agent index with the create_index tool `--runs` times (dropping it in between),
       for the median build_ms, and keeps the last one.
    4. Times a fixed set of full-text queries over the slice in three modes, interleaved, `--runs`
       times each: `scan` (no inverted index: a whole-word regex on the base table, restricted to
       the slice by ts), `full` (the Dataset's full index) and `agent` (the agent index, resolved
       through corpus.datasets.relation(), which also proves the routing). Every mode must return
       the same rows, or the benchmark stops.
    5. Times count_by and trend again (now answered by the agent index) and checks the numbers are
       identical, then drops the agent index with the drop_index tool, also after an error.
    6. Writes the report (`--out`) with medians, every run, the break-even number of queries and
       the environment.

Query times are server-side execution times from EXPLAIN ANALYZE ("Total Time"): the client wall
time also contains the network round trip, which is the same in every mode and on the shared dev
instance is larger than the differences being measured. Wall medians are reported next to them.
The build time is the wall time create_index reports (build_ms = CREATE INDEX + VACUUM
(REFRESH_TABLE)), so it includes one round trip per statement and the break-even is conservative.

It builds at most one agent index at a time over at most index_admin.MAX_SLICE_ROWS Log Entries.
"""

from __future__ import annotations

import argparse
import math
import platform
import re
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

import anyio
import psycopg
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from serenedb_log_mcp.config import ConfigError, Settings, load_settings, set_settings
from serenedb_log_mcp.corpus import agent_indexes, facts
from serenedb_log_mcp.corpus.datasets import Relation, relation
from serenedb_log_mcp.evidence import DataScope
from serenedb_log_mcp.index_admin import existing_agent_indexes
from serenedb_log_mcp.server import create_server

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "results" / "index-tradeoff.md"
# The /p/gb1 Lustre mount-failure wave (KERNMNTF) that leads the worked investigation (#5): four
# weeks of BGL holding 310,868 Log Entries.
DEFAULT_SLICE = ("2005-08-22", "2005-09-19")
RTT_SAMPLES = 11
RECONNECT_WAIT_S = 60.0
CONNECT_TIMEOUT_S = 10

Mode = Literal["scan", "full", "agent"]
MODES: tuple[Mode, ...] = ("scan", "full", "agent")
MODE_TITLES = {"scan": "scan (no index)", "full": "full index", "agent": "agent index"}
Shape = Literal["count", "by_source", "by_day", "first_rows"]

_TOTAL_TIME_RE = re.compile(r"Total Time: ([0-9.]+)s")
_T = TypeVar("_T")


class BenchError(RuntimeError):
    """The benchmark cannot produce a trustworthy number."""


@dataclass(frozen=True)
class Query:
    """One full-text question over the slice, in one result shape."""

    words: tuple[str, ...]
    shape: Shape

    @property
    def text(self) -> str:
        return " ".join(self.words)


SHAPES: tuple[Shape, ...] = ("count", "by_source", "by_day", "first_rows")
TERMS = (("lustre", "mount", "failed"), ("ciod",))
"""The wave's own Pattern (KERNMNTF, "Lustre mount FAILED") and the most frequent APP Component word
in the slice. Both give identical counts from the regex scan and from the index tokenizer, which
the benchmark re-checks on every run; a word such as "error" does not (it also matches inside
tokens that split_text keeps whole)."""
QUERIES = tuple(Query(words, shape) for words in TERMS for shape in SHAPES)


# Pure parts -----------------------------------------------------------------------------------------


def total_time_ms(plan: str) -> float:
    """The server-side execution time of an EXPLAIN ANALYZE plan, in milliseconds."""
    match = _TOTAL_TIME_RE.search(plan)
    if match is None:
        raise BenchError(f"EXPLAIN ANALYZE printed no 'Total Time': {plan[:200]!r}")
    return float(match.group(1)) * 1000


def break_even(build_ms: float, saved_ms_per_query: float) -> int | None:
    """Queries after which the build time is repaid; None when the index saves nothing."""
    if saved_ms_per_query <= 0:
        return None
    return math.ceil(build_ms / saved_ms_per_query)


def query_sql(
    query: Query, rel: Relation, mode: Mode, start: datetime, end: datetime, *, index: sql.Composable | None
) -> sql.Composed:
    """The statement for one query in one mode. Terms and bounds are fixed constants, so literals."""
    message, ts, source = rel.column("message"), rel.column("time"), rel.column("source")
    if mode == "scan":
        entries: sql.Composable = rel.table
        match = sql.SQL(" AND ").join(
            sql.SQL("regexp_matches(e.{}, {})").format(
                message, sql.Literal(f"(?i)(^|[^a-z0-9]){w}([^a-z0-9]|$)")
            )
            for w in query.words
        )
    else:
        if index is None:
            raise BenchError(f"mode {mode!r} needs an index")
        entries = index
        match = sql.SQL("e.{} @@ websearch_to_tsquery({})").format(message, sql.Literal(query.text))
    where = sql.SQL("{} AND e.{} >= {} AND e.{} < {}").format(
        match, ts, sql.Literal(start), ts, sql.Literal(end)
    )
    templates: dict[Shape, str] = {
        "count": "SELECT count(*) FROM {src} AS e WHERE {where}",
        "by_source": "SELECT e.{source} AS source, count(*) AS n FROM {src} AS e WHERE {where} "
        "GROUP BY 1 ORDER BY n DESC, source LIMIT 10",
        "by_day": "SELECT time_bucket(INTERVAL '1 day', e.{ts}) AS day, count(*) AS n FROM {src} AS e "
        "WHERE {where} GROUP BY 1 ORDER BY 1",
        "first_rows": "SELECT e.entry_id, e.{ts}, e.{source}, e.{message} FROM {src} AS e WHERE {where} "
        "ORDER BY e.{ts}, e.entry_id LIMIT 20",
    }
    return sql.SQL(templates[query.shape]).format(
        src=entries, where=where, source=source, ts=ts, message=message
    )


# The report -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Build:
    """One create_index run."""

    create_ms: float
    refresh_ms: float

    @property
    def build_ms(self) -> float:
        return self.create_ms + self.refresh_ms


@dataclass(frozen=True)
class Timed:
    """One query: matching Log Entries, and its server and wall times per mode (one value per run)."""

    query: Query
    matches: int
    server: dict[str, list[float]]
    wall: dict[str, list[float]]

    def median(self, mode: str) -> float:
        return statistics.median(self.server[mode])

    def wall_median(self, mode: str) -> float:
        return statistics.median(self.wall[mode])


@dataclass(frozen=True)
class ToolTiming:
    """One tool call's Evidence query_ms without and with the agent index (one value per run)."""

    tool: str
    call: str
    without: list[float]
    with_index: list[float]
    identical: bool


def _ms(value: float) -> str:
    return f"{value:,.1f} ms"


def _runs(values: Sequence[float]) -> str:
    return ", ".join(f"{v:.1f}" for v in values)


def _queries(n: int | None) -> str:
    return "never (the index saves no time per query)" if n is None else f"{n:,} queries"


@dataclass
class Report:
    """Everything the benchmark measured, and its Markdown rendering."""

    dataset: str
    time_from: datetime
    time_to: datetime
    slice_rows: int
    dataset_rows: int
    builds: list[Build]
    queries: list[Timed]
    tools: list[ToolTiming]
    environment: dict[str, str] = field(default_factory=dict)

    @property
    def build_ms(self) -> float:
        return statistics.median(b.build_ms for b in self.builds)

    def mean(self, mode: str) -> float:
        return statistics.fmean(t.median(mode) for t in self.queries)

    def saved(self, baseline: str) -> float:
        """Mean server time saved per query by the agent index against `baseline`."""
        return statistics.fmean(t.median(baseline) - t.median("agent") for t in self.queries)

    def markdown(self) -> str:
        span = f"{self.time_from.isoformat()} to {self.time_to.isoformat()}"
        median_build = min(self.builds, key=lambda b: abs(b.build_ms - self.build_ms))
        vs_scan, vs_full = (
            break_even(self.build_ms, self.saved("scan")),
            break_even(self.build_ms, self.saved("full")),
        )
        lines = [
            "# Agent-managed index: build time against query time saved (T19)",
            "",
            "Generated by `make bench-index` (`bench/index_tradeoff.py`); do not edit by hand.",
            "",
            "## Result",
            "",
            f"- **Slice:** {self.dataset} from {span}, {self.slice_rows:,} of the Dataset's "
            f"{self.dataset_rows:,} Log Entries.",
            f"- **Build** (`create_index`, median of {len(self.builds)}): {_ms(self.build_ms)} until the "
            f"index is usable (CREATE INDEX {_ms(median_build.create_ms)} + VACUUM (REFRESH_TABLE) "
            f"{_ms(median_build.refresh_ms)}); runs: {_runs([b.build_ms for b in self.builds])} ms.",
            f"- **Queries:** {len(self.queries)} full-text queries over the slice; mean server time per "
            f"query: {_ms(self.mean('scan'))} without an inverted index (scan), "
            f"{_ms(self.mean('full'))} with the Dataset's full index, {_ms(self.mean('agent'))} with the "
            "agent index.",
            f"- **Break-even against a scan without an inverted index:** the agent index saves "
            f"{_ms(self.saved('scan'))} per query, so it repays its build time after {_queries(vs_scan)}.",
            f"- **Break-even against the Dataset's full index:** it saves {_ms(self.saved('full'))} per "
            f"query, so it repays its build time after {_queries(vs_full)}.",
            *self._highlights(),
            "",
            "## Queries",
            "",
            f"Server-side execution time (EXPLAIN ANALYZE), median of {self._runs_per()} runs, in ms. "
            "`saved` is the median of the mode minus the median with the agent index; `break-even` is "
            "the build time divided by that saving. The wall columns are client medians including the "
            "network round trip.",
            "",
            "| query | shape | matching Log Entries | scan (no index) | full index | agent index "
            "| saved vs scan | saved vs full | break-even vs scan | break-even vs full "
            "| wall: scan / full / agent |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for t in self.queries:
            scan, full, agent = t.median("scan"), t.median("full"), t.median("agent")
            lines.append(
                f"| {t.query.text} | {t.query.shape} | {t.matches:,} | {scan:.1f} | {full:.1f} | "
                f"{agent:.1f} | {scan - agent:.1f} | {full - agent:.1f} | "
                f"{_count(break_even(self.build_ms, scan - agent))} | "
                f"{_count(break_even(self.build_ms, full - agent))} | "
                f"{t.wall_median('scan'):.0f} / {t.wall_median('full'):.0f} / {t.wall_median('agent'):.0f} |"
            )
        lines += ["", "Every run, server ms per mode:", ""]
        for t in self.queries:
            runs = "; ".join(f"{MODE_TITLES[m]} {_runs(t.server[m])}" for m in MODES)
            lines.append(f"- {t.query.text} / {t.query.shape}: {runs}")
        lines += [
            "",
            "## Through the tools",
            "",
            "The same Data Scope (the whole slice) through the MCP tools, without and with the agent "
            "index. The value is the Evidence `query_ms`: the wall time of every statement the tool "
            "ran, including one network round trip each and the whole-Dataset baseline statement, "
            "which always reads the full index. `identical` compares every row and summary value.",
            "",
            "| tool | call | without agent index (ms) | with agent index (ms) | identical numbers "
            "| runs without | runs with |",
            "|---|---|---:|---:|---|---|---|",
        ]
        for tool in self.tools:
            lines.append(
                f"| {tool.tool} | `{tool.call}` | {statistics.median(tool.without):.1f} | "
                f"{statistics.median(tool.with_index):.1f} | {'yes' if tool.identical else 'NO'} | "
                f"{_runs(tool.without)} | {_runs(tool.with_index)} |"
            )
        lines += ["", "## Environment", ""]
        lines += [f"- **{key}:** {value}" for key, value in self.environment.items()]
        lines += [
            "",
            "## Method",
            "",
            "- **scan** is what an agent can do without an inverted index: a whole-word, case-insensitive "
            "regex per query word on the base table, restricted to the slice by `ts`. The `@@` operator "
            "needs an inverted index, so there is no index-free full-text predicate to compare with.",
            "- **full index** is the Dataset's canonical inverted index with the slice as a `ts` range.",
            "- **agent index** is the partial inverted index `create_index` built "
            "(`CREATE INDEX … USING inverted (…) WHERE ts >= … AND ts < …`), resolved through "
            "`corpus.datasets.relation()` for a Data Scope inside the slice.",
            "- All three modes returned identical rows for every query on every run (checked).",
            "- Measurements are interleaved (each run times every query in every mode), so a load change "
            "on the shared server affects all modes alike. Break-even uses the median build time and "
            "the mean of the per-query medians.",
            "",
            "## Reproduce",
            "",
            "```",
            f"make bench-index BENCH_ARGS='--dataset {self.dataset} --time-from "
            f"{self.time_from.date().isoformat()} --time-to {self.time_to.date().isoformat()}'",
            "```",
            "",
        ]
        return "\n".join(lines)

    def _highlights(self) -> list[str]:
        """Where the agent index pays off fastest against the full index, and where it saves nothing."""
        if not self.queries:
            return []
        best = max(self.queries, key=lambda t: t.median("full") - t.median("agent"))
        lines = [
            f"- **Pays off fastest:** {best.query.text} / {best.query.shape} (full index "
            f"{best.median('full'):.1f} ms, agent index {best.median('agent'):.1f} ms: "
            f"{_queries(break_even(self.build_ms, best.median('full') - best.median('agent')))})."
        ]
        none = [
            f"{t.query.text} / {t.query.shape}" for t in self.queries if t.median("full") <= t.median("agent")
        ]
        if none:
            lines.append(f"- **No saving against the full index:** {', '.join(none)}.")
        return lines

    def _runs_per(self) -> int:
        return len(next(iter(self.queries[0].server.values()))) if self.queries else 0


def _count(n: int | None) -> str:
    return "never" if n is None else f"{n:,}"


# Running --------------------------------------------------------------------------------------------


class Runner:
    """One autocommit connection that reconnects once after SereneDB restarts (it SIGSEGVs at times)."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn = self._connect()

    def _connect(self) -> psycopg.Connection[tuple[Any, ...]]:
        return psycopg.connect(self.dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)

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

    def rows(self, statement: sql.Composable | str) -> list[tuple[Any, ...]]:
        text = statement if isinstance(statement, str) else statement.as_string(None)
        return self._retry(lambda: self.conn.execute(text).fetchall())

    def wall(self, statement: sql.Composable) -> tuple[float, list[tuple[Any, ...]]]:
        text = statement.as_string(None)

        def timed() -> tuple[float, list[tuple[Any, ...]]]:
            started = time.perf_counter()
            found = self.conn.execute(text).fetchall()
            return (time.perf_counter() - started) * 1000, found

        return self._retry(timed)

    def server_ms(self, statement: sql.Composable) -> float:
        plan = self.rows("EXPLAIN ANALYZE " + statement.as_string(None))
        return total_time_ms("\n".join(str(row[0]) for row in plan))


class Tools:
    """The product's MCP tools, called in process the way a client calls them."""

    def __init__(self) -> None:
        self.server: MCPServer = create_server()

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        result = anyio.run(lambda: self.server.call_tool(tool, arguments))
        if not isinstance(result, CallToolResult) or result.structured_content is None:
            raise BenchError(f"{tool} returned no Evidence: {result!r}")
        return dict(result.structured_content)


def _tool_calls(dataset: str, time_from: str, time_to: str) -> list[tuple[str, str, dict[str, Any]]]:
    scope = {"role": "engineer", "dataset": dataset, "time_from": time_from, "time_to": time_to}
    text = " ".join(TERMS[0])
    source = "node" if dataset == "bgl" else "component"
    return [
        ("count_by", f"group_by='{source}', query='{text}'", {**scope, "group_by": source, "query": text}),
        ("trend", f"bucket='day', query='{text}'", {**scope, "bucket": "day", "query": text}),
    ]


def _time_tools(
    tools: Tools, calls: list[tuple[str, str, dict[str, Any]]], runs: int, *, routed: bool
) -> list[tuple[list[float], dict[str, Any]]]:
    timed: list[tuple[list[float], dict[str, Any]]] = []
    for tool, _, arguments in calls:
        values, last = [], {}
        for _ in range(runs):
            last = tools.call(tool, **arguments)
            values.append(float(last["query_ms"]))
        if bool(last["data_scope"]["agent_indexes"]) != routed:
            raise BenchError(f"{tool}: expected routed={routed}, got {last['data_scope']['agent_indexes']}")
        timed.append((values, last))
    return timed


def _same_numbers(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return bool(a["rows"] == b["rows"] and a["summary"] == b["summary"])


def _environment(runner: Runner, settings: Settings, runs: int, note: str) -> dict[str, str]:
    version = str(runner.rows("SELECT version()")[0][0])
    samples = []
    for _ in range(RTT_SAMPLES):
        started = time.perf_counter()
        runner.rows("SELECT 1")
        samples.append((time.perf_counter() - started) * 1000)
    parts = conninfo_to_dict(settings.database_url)
    server = f"{parts.get('host', 'localhost')}:{parts.get('port', '5432')}"
    env = {
        "Date (UTC)": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "SereneDB": version,
        "Server": server + (f" ({note})" if note else ""),
        "Schema": settings.slm_schema,
        f"Round trip (SELECT 1), median of {RTT_SAMPLES}": _ms(statistics.median(samples)),
        "Client": f"{platform.platform()}, Python {platform.python_version()}, psycopg {psycopg.__version__}",
        "Runs per measurement": str(runs),
    }
    return env


def run(args: argparse.Namespace) -> Report:
    settings = load_settings(ledger_path=None)
    set_settings(settings)
    facts.clear_cache()
    agent_indexes.clear()
    dataset = args.dataset
    start, end = datetime.fromisoformat(args.time_from), datetime.fromisoformat(args.time_to)
    rel = relation(dataset, settings=settings)
    present = [index for index in existing_agent_indexes(settings=settings) if index.dataset == dataset]
    if present:
        raise BenchError(
            f"{dataset} already has the agent index {present[0].qualified_name}; this benchmark never drops "
            "an index it did not build. Drop it with drop_index first, or wait for whoever built it."
        )
    runner = Runner(settings.database_url)
    environment = _environment(runner, settings, args.runs, args.server_note)
    ts = rel.column("time")
    slice_rows = int(
        runner.rows(
            sql.SQL("SELECT count(*) FROM {} WHERE {} >= {} AND {} < {}").format(
                rel.table, ts, sql.Literal(start), ts, sql.Literal(end)
            )
        )[0][0]
    )
    dataset_rows = int(runner.rows(sql.SQL("SELECT count(*) FROM {}").format(rel.table))[0][0])
    print(f"slice: {slice_rows:,} of {dataset_rows:,} {dataset} Log Entries", file=sys.stderr)

    tools = Tools()
    calls = _tool_calls(dataset, start.isoformat(), end.isoformat())
    without = _time_tools(tools, calls, args.runs, routed=False)

    builds: list[Build] = []
    name: str | None = None
    try:
        for attempt in range(args.runs):
            created = tools.call(
                "create_index",
                role="engineer",
                dataset=dataset,
                time_from=start.isoformat(),
                time_to=end.isoformat(),
            )
            summary = created["summary"]
            if summary["status"] != "built":
                raise BenchError(f"create_index did not build a new index: {summary}")
            name = str(summary["index"])
            builds.append(Build(float(summary["create_ms"]), float(summary["refresh_ms"])))
            print(f"build {attempt + 1}: {builds[-1].build_ms:.1f} ms ({name})", file=sys.stderr)
            if attempt < args.runs - 1:
                tools.call("drop_index", role="engineer", name=name)
                name = None
        inside = DataScope(datasets=[dataset], time_from=start.isoformat(), time_to=end.isoformat())
        agent_rel = relation(dataset, scope=inside, settings=settings)
        if agent_rel.agent_index is None or agent_rel.agent_index.name != name:
            raise BenchError(f"the resolver did not route the slice to {name}: {agent_rel.index_name}")
        queries = _time_queries(runner, rel, agent_rel, start, end, args.runs)
        with_index = _time_tools(tools, calls, args.runs, routed=True)
    finally:
        if name is not None:
            tools.call("drop_index", role="engineer", name=name)
            print(f"dropped {name}", file=sys.stderr)
    timings = [
        ToolTiming(tool, call, w_values, x_values, _same_numbers(w_last, x_last))
        for (tool, call, _), (w_values, w_last), (x_values, x_last) in zip(
            calls, without, with_index, strict=True
        )
    ]
    return Report(dataset, start, end, slice_rows, dataset_rows, builds, queries, timings, environment)


def _time_queries(
    runner: Runner, rel: Relation, agent_rel: Relation, start: datetime, end: datetime, runs: int
) -> list[Timed]:
    indexes: dict[Mode, sql.Composable | None] = {"scan": None, "full": rel.index, "agent": agent_rel.index}
    statements = {
        (query, mode): query_sql(query, rel, mode, start, end, index=indexes[mode])
        for query in QUERIES
        for mode in MODES
    }
    server: dict[tuple[Query, Mode], list[float]] = {key: [] for key in statements}
    wall: dict[tuple[Query, Mode], list[float]] = {key: [] for key in statements}
    results: dict[tuple[Query, Mode], list[tuple[Any, ...]]] = {}
    for attempt in range(runs):
        for query in QUERIES:
            for mode in MODES:
                key = (query, mode)
                server[key].append(runner.server_ms(statements[key]))
                elapsed, found = runner.wall(statements[key])
                wall[key].append(elapsed)
                results.setdefault(key, found)
                if found != results[(query, "scan")]:  # the scan runs first in every run
                    raise BenchError(
                        f"{mode} returned other rows than the scan for {query.text} / {query.shape}"
                    )
        print(f"query run {attempt + 1} of {runs} done", file=sys.stderr)
    timed = []
    for query in QUERIES:
        count_rows = results[(Query(query.words, "count"), "scan")]
        timed.append(
            Timed(
                query,
                int(count_rows[0][0]),
                {mode: server[(query, mode)] for mode in MODES},
                {mode: wall[(query, mode)] for mode in MODES},
            )
        )
    return timed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent-managed index benchmark (T19).")
    parser.add_argument("--dataset", choices=["bgl", "openstack"], default="bgl")
    parser.add_argument("--time-from", default=DEFAULT_SLICE[0], help="slice start, ISO-8601 (inclusive)")
    parser.add_argument("--time-to", default=DEFAULT_SLICE[1], help="slice end, ISO-8601 (exclusive)")
    parser.add_argument("--runs", type=int, default=3, help="runs per measurement (default 3, medians)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report path")
    parser.add_argument("--server-note", default="", help="a note on the server for the environment section")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    try:
        report = run(args)
    except (BenchError, ConfigError, psycopg.Error) as exc:
        print(f"bench-index: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report.markdown())
    vs_scan = break_even(report.build_ms, report.saved("scan"))
    vs_full = break_even(report.build_ms, report.saved("full"))
    print(
        f"build {report.build_ms:.1f} ms; per query scan {report.mean('scan'):.1f} / full "
        f"{report.mean('full'):.1f} / agent {report.mean('agent'):.1f} ms; break-even vs scan "
        f"{_queries(vs_scan)}, vs full {_queries(vs_full)}; wrote {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
