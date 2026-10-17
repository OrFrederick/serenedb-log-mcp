"""`latency_stats`: OpenStack timing questions that text search cannot answer (#1, #2, plan T11).

Three views, all over columns the derive step precomputed, so no regex runs at query time:

- percentiles per group (endpoint, source or the whole Data Scope): n, p50, p95, p99 and max,
  computed by `quantile_cont`;
- Latency Degradation: the same per time bucket, each bucket compared with a baseline (the median
  of the per-bucket p95 and p99) and flagged when it rose by `rise_factor`;
- Phase-Duration Anomalies: instances ranked by spawn or build time, with the robust z-scores
  (median and MAD over every instance) that the derive step stored, flagged at `min_z`.

The instance phases come from the resolver's `instances_table`; which instances are labelled is
never known here (the labels are read only by scripts/ground_truth.py).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp.corpus.datasets import Relation, relation
from serenedb_log_mcp.db import QueryResult, fetch_all, fetch_limited
from serenedb_log_mcp.evidence import Role, RoleParam
from serenedb_log_mcp.scope import ScopeError, build_scope, resolve_time, time_bounds, validate_choice
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool

__all__ = [
    "BUCKETS",
    "DEFAULT_MIN_Z",
    "DEFAULT_RISE_FACTOR",
    "MAX_GROUPS",
    "MIN_BUCKET_SAMPLES",
    "View",
    "canonical_endpoint",
    "register",
    "resolve_view",
]

DATASET = "openstack"
MAX_GROUPS = 50
"""At most this many groups, buckets or instances per call (#2 Decision proposal)."""
DEFAULT_LIMIT = 25
MIN_BUCKET_SAMPLES = 20
"""A bucket with fewer samples neither rises nor counts towards the baseline: its p95 is noise."""
DEFAULT_RISE_FACTOR = 2.0
DEFAULT_MIN_Z = 5.0
"""The robust z-score from which an instance is a Phase-Duration Anomaly (the #1 research cut).

The start-up phases vary little (the MAD of spawn_s is 0.42 s on a 19.7 s median in the Base
Corpus), so the textbook 3.5 flags instances that took 1.5 s longer than usual. scripts/ground_truth.py
reports the result at 3.5, 5 and 8.
"""
DIGITS = 4
"""Seconds are rounded to 0.1 ms: API latencies go down to about 1 ms."""

Metric = Literal["api", "spawn", "build"]
GroupBy = Literal["endpoint", "source", "bucket", "instance", "none"]
Bucket = Literal["minute", "5min", "15min", "hour", "day"]
BUCKETS: dict[str, str] = {
    "minute": "1 minute",
    "5min": "5 minutes",
    "15min": "15 minutes",
    "hour": "1 hour",
    "day": "1 day",
}
DEFAULT_BUCKET = "hour"

_UNITS = {"api": "API requests", "spawn": "instances", "build": "instances"}
_WHAT = {
    "api": "the time of each nova-api request Log Entry (latency_s), in seconds",
    "spawn": "nova's own 'Took N seconds to spawn the instance on the hypervisor', one value per instance",
    "build": "nova's own 'Took N seconds to build instance', one value per instance",
}
_GROUP_LABELS = {"endpoint": "endpoint", "source": "source_id", "none": "scope"}
_INSTANCE_COLUMNS = (
    "instance_id",
    "source_id",
    "first_ts",
    "spawn_ts",
    "spawn_s",
    "spawn_z",
    "build_s",
    "build_z",
    "image_s",
    "image_z",
    "paused_s",
    "paused_z",
)
_RELATIVE_RE = re.compile(r"^\s*(?:last|past)\b", re.IGNORECASE)


# The view a call asks for ----------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """What one call computes: the metric, how it is grouped, and the bucket width for buckets."""

    metric: str
    group_by: str
    bucket: str | None


def resolve_view(metric: str, group_by: str | None, bucket: str | None) -> View:
    """The view of a call, with the defaults filled in; ScopeError for arguments that contradict.

    Without group_by, a bucket means Latency Degradation, otherwise API latency is grouped by
    endpoint and spawn or build time is listed per instance.
    """
    if group_by is None:
        group_by = "bucket" if bucket is not None else "endpoint" if metric == "api" else "instance"
    if group_by == "endpoint" and metric != "api":
        raise ScopeError(
            f"group_by='endpoint' groups API requests, but metric={metric!r} measures instances. Use "
            "metric='api' for latency per endpoint, or group_by='instance', 'source', 'bucket' or "
            f"'none' for {metric} time."
        )
    if group_by == "instance" and metric == "api":
        raise ScopeError(
            "group_by='instance' lists instances by their spawn or build time, so it needs "
            "metric='spawn' or metric='build'. For API latency use group_by='endpoint', 'source', "
            "'bucket' or 'none'."
        )
    if group_by != "bucket":
        if bucket is not None:
            raise ScopeError(
                f"bucket={bucket!r} sets the width of time buckets, which only group_by='bucket' uses "
                f"(Latency Degradation). Drop bucket, or call with group_by='bucket'."
            )
        return View(metric, group_by, None)
    return View(metric, group_by, bucket or DEFAULT_BUCKET)


# Endpoints -------------------------------------------------------------------------------------------

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TENANT_RE = re.compile(r"[0-9a-f]{32}")
_NUMBER_RE = re.compile(r"/[0-9]+(/|$)")
_HTTP_VERSION_RE = re.compile(r"\s+HTTP/[0-9.]+$", re.IGNORECASE)


def canonical_endpoint(value: str) -> str:
    """An endpoint as the derive step stores it: the method and the path with ids masked.

    UUIDs become {uuid}, 32-hex tenant ids {tenant} and numeric path segments {n}, so a path copied
    from a Log Entry finds its endpoint. A query string and a trailing HTTP version are dropped.
    """
    text = _HTTP_VERSION_RE.sub("", value.strip().strip('"').strip())
    method, _, path = text.partition(" ")
    if not path.strip():
        method, path = "", text
    path = path.strip().split("?", 1)[0]
    path = _TENANT_RE.sub("{tenant}", _UUID_RE.sub("{uuid}", path))
    for _ in range(2):  # adjacent numeric segments share a slash, as in the derive step
        path = _NUMBER_RE.sub(r"/{n}\1", path)
    return f"{method.upper()} {path}" if method else path


_endpoints: dict[str, tuple[str, ...]] = {}
_endpoints_lock = threading.Lock()


def _known_endpoints(rel: Relation, role: Role) -> tuple[tuple[str, ...], QueryResult | None]:
    """The endpoints of the Dataset, once per table and process, and the query a cache miss ran.

    The query goes into the Evidence like every other statement of the call; a cache hit runs none.
    """
    with _endpoints_lock:
        cached = _endpoints.get(rel.table_name)
    if cached is not None:
        return cached, None
    column = rel.column("endpoint")
    statement = sql.SQL("SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL ORDER BY 1").format(
        c=column, t=rel.table
    )
    result = fetch_all(statement.as_string(None), role=role)
    found = tuple(str(row[0]) for row in result.rows)
    with _endpoints_lock:
        return _endpoints.setdefault(rel.table_name, found), result


# SQL pieces ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Q:
    """A piece of SQL and the values of its %s placeholders, in order."""

    sql: sql.Composable
    params: tuple[Any, ...] = ()

    @property
    def text(self) -> str:
        return self.sql.as_string(None)


def _q(template: str, *parts: _Q | sql.Composable) -> _Q:
    """Fill the positional {} of `template`; the parameters follow the parts' order."""
    composables: list[sql.Composable] = []
    params: list[Any] = []
    for part in parts:
        if isinstance(part, _Q):
            composables.append(part.sql)
            params.extend(part.params)
        else:
            composables.append(part)
    return _Q(sql.SQL(template).format(*composables), tuple(params))


def _bound(value: Any, cast: str = "") -> _Q:
    return _Q(sql.SQL("%s" + cast), (value,))


def _all(conditions: Sequence[_Q]) -> _Q:
    return _Q(
        sql.SQL(" AND ").join(c.sql for c in conditions), tuple(v for c in conditions for v in c.params)
    )


@dataclass(frozen=True)
class _Filters:
    start: datetime | None
    end: datetime | None
    endpoint: str | None
    pattern_ids: list[str] | None
    family_id: str | None


def _samples(rel: Relation, metric: str, filters: _Filters) -> _Q:
    """One row per sample: its time, endpoint, source and value (column v)."""
    if metric == "api":
        ts = rel.column("time")
        value = rel.column("latency_s")
        select = _q(
            "SELECT {} AS ts, {} AS endpoint, {} AS source_id, {} AS v FROM {}",
            ts,
            rel.column("endpoint"),
            rel.column("source_id"),
            value,
            rel.table,
        )
        conditions = [_q("{} IS NOT NULL", value)]
        if filters.endpoint is not None:
            conditions.append(_q("{} = {}", rel.column("endpoint"), _bound(filters.endpoint)))
        if filters.pattern_ids:
            conditions.append(
                _q("{} = ANY({})", rel.column("template_id"), _bound(filters.pattern_ids, "::VARCHAR[]"))
            )
        if filters.family_id is not None:
            conditions.append(_q("{} = {}", rel.column("family_id"), _bound(filters.family_id)))
    else:
        ts = sql.Identifier("first_ts")
        value = sql.Identifier(f"{metric}_s")
        select = _q(
            "SELECT {} AS ts, CAST(NULL AS VARCHAR) AS endpoint, {} AS source_id, {} AS v FROM {}",
            ts,
            sql.Identifier("source_id"),
            value,
            rel.instances_table,
        )
        conditions = [_q("{} IS NOT NULL", value)]
    if filters.start is not None:
        conditions.append(_q("{} >= {}", ts, _bound(filters.start)))
    if filters.end is not None:
        conditions.append(_q("{} < {}", ts, _bound(filters.end)))
    return _q("{} WHERE {}", select, _all(conditions))


_PERCENTILES = sql.SQL(
    "count(*) AS n, "
    "round(quantile_cont(v, 0.5), {d}) AS p50_s, "
    "round(quantile_cont(v, 0.95), {d}) AS p95_s, "
    "round(quantile_cont(v, 0.99), {d}) AS p99_s, "
    "round(max(v), {d}) AS max_s"
).format(d=sql.Literal(DIGITS))
_STAT_COLUMNS = ["n", "p50_s", "p95_s", "p99_s", "max_s"]


def _stats(row: Sequence[Any]) -> dict[str, Any]:
    return dict(zip(_STAT_COLUMNS, row, strict=True))


# The three views -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Computed:
    parts: list[QueryResult]
    total: int
    rows: list[tuple[Any, ...]]
    columns: list[str]
    summary: dict[str, Any]
    notes: list[str]
    truncation_note: str


def _groups(rel: Relation, role: Role, view: View, filters: _Filters, limit: int) -> _Computed:
    samples = _samples(rel, view.metric, filters)
    label = _GROUP_LABELS[view.group_by]
    key: sql.Composable = sql.Literal("all") if view.group_by == "none" else sql.Identifier(label)
    count = _q("WITH samples AS ({}) SELECT count(DISTINCT {}) FROM samples", samples, key)
    rows = _q(
        "WITH samples AS ({}) SELECT {} AS {}, {} FROM samples GROUP BY 1 ORDER BY n DESC, 1 LIMIT %s",
        samples,
        key,
        sql.Identifier(label),
        _PERCENTILES,
    )
    overall = _q("WITH samples AS ({}) SELECT {}, min(ts), max(ts) FROM samples", samples, _PERCENTILES)
    limited = fetch_limited(count.text, rows.text, list(count.params), limit, role=role)
    totals = fetch_all(overall.text, overall.params, role=role)
    first = totals.rows[0]
    unit = _UNITS[view.metric]
    summary = {
        "metric": view.metric,
        "unit": unit,
        f"{_unit_key(view.metric)}_total": int(first[0]),
        "groups_total": limited.total,
        "overall": _stats(first[:5]) if first[0] else None,
        "first_ts": first[5],
        "last_ts": first[6],
    }
    notes = [f"Percentiles are quantile_cont over {_WHAT[view.metric]}."]
    plural = {"endpoint": "endpoints", "source": "sources", "none": "scopes"}[view.group_by]
    truncation = (
        f"Showing the {len(limited.rows)} {plural} with the most {unit} of {limited.total}; "
        "summary.overall holds the percentiles over all of them."
    )
    return _Computed(
        [limited, totals], limited.total, limited.rows, [label, *_STAT_COLUMNS], summary, notes, truncation
    )


def _bucket_cte(
    rel: Relation, view: View, filters: _Filters, baseline: _Filters | None, rise_factor: float
) -> _Q:
    """The WITH clause of Latency Degradation: buckets, the reference, and every bucket scored."""
    width = sql.Literal(BUCKETS[view.bucket or DEFAULT_BUCKET])
    samples = _samples(rel, view.metric, filters)
    buckets = _q(
        "SELECT time_bucket({}::INTERVAL, ts) AS bucket, count(*) AS n, quantile_cont(v, 0.5) AS p50, "
        "quantile_cont(v, 0.95) AS p95, quantile_cont(v, 0.99) AS p99, max(v) AS mx "
        "FROM samples GROUP BY 1",
        width,
    )
    if baseline is None:
        reference_input = _q("SELECT n, p95, p99 FROM buckets")
    else:
        reference_input = _q(
            "SELECT count(*) AS n, quantile_cont(v, 0.95) AS p95, quantile_cont(v, 0.99) AS p99 "
            "FROM ({}) AS reference_samples GROUP BY time_bucket({}::INTERVAL, ts)",
            _samples(rel, view.metric, baseline),
            width,
        )
    return _q(
        "WITH samples AS ({}), buckets AS ({}), "
        "reference AS (SELECT median(p95) FILTER (WHERE n >= {}) AS base_p95, "
        "median(p99) FILTER (WHERE n >= {}) AS base_p99, count(*) FILTER (WHERE n >= {}) AS base_buckets, "
        "coalesce(sum(n), 0) AS base_samples FROM ({}) AS reference_buckets), "
        "scored AS (SELECT b.bucket, b.n, b.p50, b.p95, b.p99, b.mx, "
        "b.p95 / nullif(r.base_p95, 0) AS p95_ratio, b.p99 / nullif(r.base_p99, 0) AS p99_ratio, "
        "coalesce(b.n >= {} AND (b.p95 >= {} * r.base_p95 OR b.p99 >= {} * r.base_p99), false) AS rise "
        "FROM buckets AS b CROSS JOIN reference AS r)",
        samples,
        buckets,
        _bound(MIN_BUCKET_SAMPLES),
        _bound(MIN_BUCKET_SAMPLES),
        _bound(MIN_BUCKET_SAMPLES),
        reference_input,
        _bound(MIN_BUCKET_SAMPLES),
        _bound(rise_factor),
        _bound(rise_factor),
    )


def _degradation(
    rel: Relation,
    role: Role,
    view: View,
    filters: _Filters,
    baseline: _Filters | None,
    rise_factor: float,
    limit: int,
) -> _Computed:
    width = sql.Literal(BUCKETS[view.bucket or DEFAULT_BUCKET])
    cte = _bucket_cte(rel, view, filters, baseline, rise_factor)
    count = _q(
        "WITH samples AS ({}) SELECT count(DISTINCT time_bucket({}::INTERVAL, ts)) FROM samples",
        _samples(rel, view.metric, filters),
        width,
    )
    rows = _q(
        "{} SELECT bucket, n, round(p50, {}) AS p50_s, round(p95, {}) AS p95_s, round(p99, {}) AS p99_s, "
        "round(mx, {}) AS max_s, round(p95_ratio, 2) AS p95_ratio, round(p99_ratio, 2) AS p99_ratio, rise "
        "FROM (SELECT * FROM scored ORDER BY rise DESC, greatest(p95_ratio, p99_ratio) DESC NULLS LAST, "
        "bucket LIMIT %s) AS kept ORDER BY bucket",
        cte,
        *([sql.Literal(DIGITS)] * 4),
    )
    summary_q = _q(
        "{} SELECT count(*), sum(n), "
        "(SELECT round(base_p95, {}) FROM reference), (SELECT round(base_p99, {}) FROM reference), "
        "(SELECT base_buckets FROM reference), (SELECT base_samples FROM reference), "
        "count(*) FILTER (WHERE rise), "
        "min(bucket) FILTER (WHERE rise), max(bucket) FILTER (WHERE rise) FROM scored",
        cte,
        sql.Literal(DIGITS),
        sql.Literal(DIGITS),
    )
    limited = fetch_limited(
        count.text, rows.text, list(count.params), limit, role=role, rows_params=[*rows.params, limit]
    )
    totals = fetch_all(summary_q.text, summary_q.params, role=role)
    buckets, samples, base_p95, base_p99, base_buckets, base_samples, rising, first_rise, last_rise = (
        totals.rows[0]
    )
    unit = _UNITS[view.metric]
    summary = {
        "metric": view.metric,
        "unit": unit,
        "bucket": view.bucket,
        "buckets_total": int(buckets),
        f"{_unit_key(view.metric)}_total": int(samples or 0),
        "baseline_p95_s": base_p95,
        "baseline_p99_s": base_p99,
        "baseline_buckets": int(base_buckets or 0),
        "baseline_samples": int(base_samples or 0),
        "baseline": "the baseline period" if baseline is not None else "the Data Scope",
        "rise_factor": rise_factor,
        "min_bucket_samples": MIN_BUCKET_SAMPLES,
        "rising_total": int(rising or 0),
        "first_rising_bucket": first_rise,
        "last_rising_bucket": last_rise,
    }
    notes = [
        f"Latency Degradation: per {view.bucket} bucket, quantile_cont over {_WHAT[view.metric]}. The "
        f"baseline is the median of the per-bucket p95 and p99 over the buckets of "
        f"{summary['baseline']} that hold at least {MIN_BUCKET_SAMPLES} {unit}; a bucket rises when it "
        f"holds at least {MIN_BUCKET_SAMPLES} {unit} and its p95 or p99 is at least {rise_factor:g} "
        "times the baseline. Buckets without samples are left out.",
    ]
    if base_p95 is None and baseline is not None and not base_samples:
        notes.append(
            f"No baseline: the baseline period holds no {unit}, so no bucket can rise. Choose a baseline "
            "period inside the OpenStack time range (call describe_corpus for it)."
        )
    elif base_p95 is None:
        notes.append(
            f"No baseline: no bucket of {summary['baseline']} holds {MIN_BUCKET_SAMPLES} or more {unit}, "
            "so no bucket can rise. Use a wider bucket or a longer time range."
        )
    truncation = (
        f"Showing the {len(limited.rows)} steepest of {limited.total} buckets (rising buckets first, then "
        "the highest p95 or p99 ratio), in time order; summary.rising_total counts every rising bucket."
    )
    columns = ["bucket", *_STAT_COLUMNS, "p95_ratio", "p99_ratio", "rise"]
    return _Computed([limited, totals], limited.total, limited.rows, columns, summary, notes, truncation)


def _instances(
    rel: Relation, role: Role, view: View, filters: _Filters, min_z: float, limit: int
) -> _Computed:
    table = rel.instances_table
    value = sql.Identifier(f"{view.metric}_s")
    z = sql.Identifier(f"{view.metric}_z")
    ts = sql.Identifier("first_ts")
    conditions = [_q("{} IS NOT NULL", value)]
    if filters.start is not None:
        conditions.append(_q("{} >= {}", ts, _bound(filters.start)))
    if filters.end is not None:
        conditions.append(_q("{} < {}", ts, _bound(filters.end)))
    where = _all(conditions)
    shown = sql.SQL(", ").join(
        sql.SQL("round({c}, 1) AS {c}").format(c=sql.Identifier(c)) if c.endswith("_z") else sql.Identifier(c)
        for c in _INSTANCE_COLUMNS
    )
    count = _q("SELECT count(*) FROM {} WHERE {}", table, where)
    rows = _q(
        "SELECT {}, coalesce({} >= {}, false) AS flagged FROM {} WHERE {} "
        "ORDER BY {} DESC, instance_id LIMIT %s",
        shown,
        z,
        _bound(min_z),
        table,
        where,
        value,
    )
    summary_q = _q(
        "SELECT count(*), count(*) FILTER (WHERE {} >= {}), "
        "(SELECT round(median({}), {}) FROM {}), (SELECT round(mad({}), {}) FROM {}), "
        "(SELECT count({}) FROM {}) FROM {} WHERE {}",
        z,
        _bound(min_z),
        value,
        sql.Literal(DIGITS),
        table,
        value,
        sql.Literal(DIGITS),
        table,
        value,
        table,
        table,
        where,
    )
    limited = fetch_limited(
        count.text, rows.text, list(count.params), limit, role=role, rows_params=[*rows.params, limit]
    )
    totals = fetch_all(summary_q.text, summary_q.params, role=role)
    instances, flagged, median, mad, reference = totals.rows[0]
    phase = "spawn_s" if view.metric == "spawn" else "build_s"
    summary = {
        "metric": view.metric,
        "unit": "instances",
        "instances_total": int(instances),
        "flagged_total": int(flagged or 0),
        "min_z": min_z,
        "median_s": median,
        "mad_s": mad,
        "reference_instances": int(reference),
        "z_formula": f"({phase} - median_s) / (1.4826 * mad_s)",
    }
    notes = [
        f"Ranked by {phase}, {_WHAT[view.metric]}. Each *_z column is a robust z-score, (x - median) / "
        f"(1.4826 * MAD), against all {int(reference):,} instances of the Dataset that have the phase "
        "(computed when the corpus was derived; a time range narrows the instances listed, not the "
        f"reference). flagged means {view.metric}_z >= {min_z:g}: a Phase-Duration Anomaly.",
        "image_s (Creating image to VM Started: image creation and boot) and paused_s (VM Paused to VM "
        "Resumed: waiting for the network) show which part of the start-up took the time.",
        "A Phase-Duration Anomaly is an Observed Fact about timing only. These Log Entries carry no ERROR "
        "for it, so any cause (an injected fault, host load, a slow image backend) is a Hypothesis.",
    ]
    truncation = (
        f"Showing the {len(limited.rows)} slowest of {limited.total} instances by {phase}; flagged ones come "
        "first, and summary.flagged_total counts all of them."
    )
    columns = [*_INSTANCE_COLUMNS, "flagged"]
    return _Computed([limited, totals], limited.total, limited.rows, columns, summary, notes, truncation)


def _unit_key(metric: str) -> str:
    return "requests" if metric == "api" else "instances"


# Arguments -------------------------------------------------------------------------------------------


def _check_filters(
    view: View,
    endpoint: str | None,
    pattern_ids: list[str] | None,
    family_id: str | None,
) -> None:
    given = [
        name
        for name, value in (("endpoint", endpoint), ("pattern_ids", pattern_ids), ("family_id", family_id))
        if value
    ]
    if view.metric != "api" and given:
        raise ScopeError(
            f"{', '.join(given)} filter API request Log Entries, so they apply to metric='api' only; "
            f"metric={view.metric!r} measures instances. Drop them, or use a time range to narrow the "
            "instances."
        )


def _baseline_bounds(
    view: View, baseline_from: str | None, baseline_to: str | None
) -> tuple[datetime | None, datetime | None] | None:
    if baseline_from is None and baseline_to is None:
        return None
    if view.group_by != "bucket":
        raise ScopeError(
            "baseline_from and baseline_to set the baseline of Latency Degradation, so they need "
            "group_by='bucket' (with a bucket such as 'hour')."
        )
    for name, value in (("baseline_from", baseline_from), ("baseline_to", baseline_to)):
        if value is None:
            raise ScopeError(
                "Give both baseline_from and baseline_to as ISO-8601 timestamps, for example "
                "baseline_from='2017-05-16T00:00:00' and baseline_to='2017-05-16T06:00:00'."
            )
        if _RELATIVE_RE.match(value):
            raise ScopeError(
                f"{name}={value!r} is relative; the baseline period needs ISO-8601 timestamps, for example "
                "'2017-05-16T00:00:00'."
            )
    window = resolve_time(baseline_from, baseline_to)
    return window.start, window.end


def _corpus_end(rel: Relation, role: Role) -> QueryResult:
    statement = sql.SQL("SELECT max({}) FROM {}").format(rel.column("time"), rel.table)
    return fetch_all(statement.as_string(None), role=role)


# The tool --------------------------------------------------------------------------------------------

MetricParam = Annotated[
    Metric,
    Field(
        description="What is timed: 'api' the latency of each OpenStack API request; 'spawn' nova's spawn "
        "time per instance; 'build' nova's build time per instance."
    ),
]
GroupByParam = Annotated[
    GroupBy | None,
    Field(
        description="How results are grouped: 'endpoint' (api only), 'source', 'bucket' (time buckets with "
        "Latency Degradation flags), 'instance' (spawn and build only: instances ranked with robust "
        "z-scores) or 'none' (one row for the whole Data Scope). Default: 'endpoint' for api, 'instance' "
        "for spawn and build, 'bucket' when bucket is given."
    ),
]
BucketParam = Annotated[
    Bucket | None,
    Field(description="Bucket width for group_by='bucket'. Default 'hour'."),
]
EndpointParam = Annotated[
    str | None,
    Field(
        description="Only this API endpoint (metric='api'), for example 'GET /v2/{tenant}/servers/detail'; "
        "a path copied from a Log Entry is masked the same way.",
        max_length=300,
    ),
]
TimeParam = Annotated[
    str | None,
    Field(
        description="ISO-8601 time without a zone, for example '2017-05-16T00:00:00', or for time_from a "
        "relative range such as 'last 6h' (it ends at the last OpenStack Log Entry). time_to is exclusive. "
        "Instances are selected by their first Log Entry.",
        max_length=64,
    ),
]
BaselineParam = Annotated[
    str | None,
    Field(
        description="With group_by='bucket': an ISO-8601 baseline period (both ends) whose buckets give the "
        "baseline, instead of the Data Scope's own buckets.",
        max_length=64,
    ),
]
RiseFactorParam = Annotated[
    float,
    Field(
        description="With group_by='bucket': a bucket rises when its p95 or p99 is at least this many "
        "times the baseline.",
        gt=1.0,
        le=100.0,
    ),
]
MinZParam = Annotated[
    float,
    Field(
        description="With group_by='instance': the robust z-score from which an instance is flagged as a "
        "Phase-Duration Anomaly.",
        ge=1.0,
        le=1000.0,
    ),
]
LimitParam = Annotated[
    int,
    Field(description="At most this many groups, buckets or instances.", ge=1, le=MAX_GROUPS),
]
PatternIdsParam = Annotated[
    list[str] | None,
    Field(
        description="metric='api' only: only API requests of these Patterns (16-hex template ids from "
        "list_patterns).",
        max_length=50,
    ),
]
FamilyIdParam = Annotated[
    str | None,
    Field(description="metric='api' only: only API requests of this Pattern Family.", max_length=32),
]


def register(server: MCPServer) -> None:
    """Register `latency_stats` on `server`."""

    @evidence_tool(server, title="OpenStack latency, Latency Degradation and Phase-Duration Anomalies")
    def latency_stats(
        role: RoleParam,
        metric: MetricParam = "api",
        group_by: GroupByParam = None,
        bucket: BucketParam = None,
        endpoint: EndpointParam = None,
        time_from: TimeParam = None,
        time_to: TimeParam = None,
        baseline_from: BaselineParam = None,
        baseline_to: BaselineParam = None,
        rise_factor: RiseFactorParam = DEFAULT_RISE_FACTOR,
        min_z: MinZParam = DEFAULT_MIN_Z,
        limit: LimitParam = DEFAULT_LIMIT,
        pattern_ids: PatternIdsParam = None,
        family_id: FamilyIdParam = None,
    ) -> ToolOutcome:
        """OpenStack timing that text search cannot answer: API latency percentiles, Latency
        Degradation over time, and instances whose spawn or build took unusually long.

        Use it for "how slow", "did latency rise" and "which instances were slow". Every number is
        computed by SereneDB over columns precomputed at load time; quote them with [E<n>].
        Inputs: role; metric ('api' request latency, 'spawn' or 'build' time per instance); group_by
        ('endpoint', 'source', 'bucket', 'instance', 'none'); bucket ('minute', '5min', '15min', 'hour',
        'day'); endpoint; time_from and time_to; baseline_from and baseline_to (a baseline period for
        buckets); rise_factor (default 2); min_z (default 5); limit (default 25, at most 50); pattern_ids
        and family_id (api only).
        Outputs, by group_by:
        - 'endpoint', 'source', 'none': one row per group with n (samples), p50_s, p95_s, p99_s and max_s
          in seconds (quantile_cont), most samples first; summary.overall covers every group.
        - 'bucket' (Latency Degradation): one row per non-empty time bucket with n, p50_s, p95_s, p99_s,
          max_s, p95_ratio and p99_ratio (against the baseline) and rise. The baseline is the median of
          the per-bucket p95 and p99 over buckets with at least 20 samples; a bucket with at least 20
          samples rises when p95 or p99 reaches rise_factor times the baseline. summary has
          baseline_p95_s, baseline_p99_s, rising_total and the first and last rising bucket. With more
          buckets than limit, the steepest are kept, in time order.
        - 'instance' (Phase-Duration Anomaly): instances, slowest first, with source_id, first_ts,
          spawn_ts, spawn_s, build_s, image_s (image creation and boot) and paused_s (waiting for the
          network), each with a robust z-score (x - median) / (1.4826 * MAD) over all instances, and
          flagged (metric z >= min_z); summary has median_s, mad_s, instances_total and flagged_total.
          A flag is an Observed Fact about timing; any cause is a Hypothesis, because these Log Entries
          carry no error for it.
        total_matches counts groups, buckets or instances.
        """
        view = resolve_view(metric, group_by, bucket)
        _check_filters(view, endpoint, pattern_ids, family_id)
        baseline_window = _baseline_bounds(view, baseline_from, baseline_to)
        rel = relation(DATASET, role)
        parts: list[QueryResult] = []
        corpus_end = None
        if time_from is not None and _RELATIVE_RE.match(time_from):
            end = _corpus_end(rel, role)
            parts.append(end)
            if end.rows and end.rows[0][0] is not None:
                corpus_end = {DATASET: end.rows[0][0]}
        canonical = None
        if endpoint is not None and endpoint.strip():
            known, lookup = _known_endpoints(rel, role)
            if lookup is not None:
                parts.append(lookup)
            canonical = validate_choice("endpoint", canonical_endpoint(endpoint), known)
        applied: dict[str, Any] = {"metric": view.metric, "group_by": view.group_by, "endpoint": canonical}
        if view.group_by == "bucket":
            applied |= {
                "bucket": view.bucket,
                "rise_factor": rise_factor,
                "baseline_from": baseline_from,
                "baseline_to": baseline_to,
            }
        if view.group_by == "instance":
            applied["min_z"] = min_z
        scope = build_scope(
            DATASET,
            time_from=time_from,
            time_to=time_to,
            corpus_end=corpus_end,
            pattern_ids=pattern_ids,
            family_id=family_id,
            **applied,
        )
        start, end_ts = time_bounds(scope)
        filters = _Filters(start, end_ts, canonical, scope.filters.pattern_ids, scope.filters.family_id)
        if view.group_by == "bucket":
            baseline = (
                None
                if baseline_window is None
                else _Filters(
                    baseline_window[0], baseline_window[1], canonical, filters.pattern_ids, filters.family_id
                )
            )
            computed = _degradation(rel, role, view, filters, baseline, rise_factor, limit)
        elif view.group_by == "instance":
            computed = _instances(rel, role, view, filters, min_z, limit)
        else:
            computed = _groups(rel, role, view, filters, limit)
        notes = list(computed.notes)
        if computed.total == 0:
            notes.append(
                f"No {_UNITS[view.metric]} matched this Data Scope. Widen the time range (call "
                "describe_corpus for the OpenStack time range) or drop the endpoint or Pattern filter."
            )
        result = QueryResult.combine(*parts, *computed.parts, total=computed.total, rows=computed.rows)
        return ToolOutcome(
            result=result,
            scope=scope,
            columns=computed.columns,
            summary=computed.summary,
            notes=notes,
            truncation_note=computed.truncation_note,
        )
