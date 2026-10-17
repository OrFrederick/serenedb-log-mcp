"""`trend`: zero-filled Log Entry counts per time bucket, optionally per group, with baselines."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import (
    ALL,
    NO_MATCH_NOTE,
    NULL_LABELS,
    AlertCategoriesParam,
    Collected,
    ComponentsParam,
    DatasetName,
    FamilyIdParam,
    LevelsParam,
    Part,
    PatternIdsParam,
    Prepared,
    Q,
    QueryParam,
    Ranked,
    SourcesParam,
    TimeFromParam,
    TimeToParam,
    baseline_of,
    baseline_summary,
    bound,
    choose_datasets,
    common_notes,
    prepare,
    q,
    rank_statement,
    read_ranked,
    read_stats,
    run,
    share,
    stats_statement,
    unassigned_pattern_notes,
    whole_weeks,
)

MAX_BUCKETS = 400
"""At most this many buckets per trend (#2 Decision proposal)."""
MAX_SERIES = 5
"""At most this many series per trend, so the result stays readable and small."""

Bucket = Literal["minute", "hour", "day", "week"]
BUCKETS: dict[str, tuple[str, timedelta]] = {
    "minute": ("1 minute", timedelta(minutes=1)),
    "hour": ("1 hour", timedelta(hours=1)),
    "day": ("1 day", timedelta(days=1)),
    "week": ("7 days", timedelta(days=7)),
}
ANCHOR = datetime(2000, 1, 3)
"""SereneDB's `time_bucket` grid origin for widths below a month (a Monday)."""
MICROSECOND = timedelta(microseconds=1)

TrendGroupBy = Literal["component", "team", "level", "node", "rack", "midplane", "alert_category", "pattern"]


def bucket_start(ts: datetime, bucket: str) -> datetime:
    """The start of the bucket `ts` falls in, as `time_bucket` computes it."""
    step = BUCKETS[bucket][1]
    return ANCHOR + ((ts - ANCHOR) // step) * step


def bucket_count(start: datetime, end: datetime, bucket: str) -> int:
    """Buckets from the one holding `start` to the one holding the last instant before `end`."""
    step = BUCKETS[bucket][1]
    return (bucket_start(end - MICROSECOND, bucket) - bucket_start(start, bucket)) // step + 1


def check_buckets(dataset: str, start: datetime, end: datetime, bucket: str) -> int:
    """The bucket count, or a ScopeError that names a bucket that fits and how to narrow the range."""
    needed = bucket_count(start, end, bucket)
    if needed <= MAX_BUCKETS:
        return needed
    names = list(BUCKETS)
    coarser = next(
        (name for name in names[names.index(bucket) + 1 :] if bucket_count(start, end, name) <= MAX_BUCKETS),
        None,
    )
    advice = (
        f"Use bucket='{coarser}' ({bucket_count(start, end, coarser):,} buckets), or narrow"
        if coarser
        else "Narrow"
    )
    raise ScopeError(
        f"trend over {dataset} from {start.isoformat()} to {end.isoformat()} needs {needed:,} {bucket} "
        f"buckets, but it returns at most {MAX_BUCKETS}. {advice} time_from and time_to to a range of "
        f"at most {MAX_BUCKETS} {bucket}s."
    )


def bucket_label(value: datetime, bucket: str) -> str:
    """A bucket start as short as the bucket allows: a date for days and weeks, else to the minute."""
    if bucket in ("day", "week"):
        return value.date().isoformat()
    return value.strftime("%Y-%m-%dT%H:%M")


def handoff_notes(series: Sequence[str], family_rows: Sequence[Sequence[Any]]) -> dict[str, str]:
    """For each series Pattern with drift partners (same family_id), the hand-off note (#1)."""
    by_family: dict[str, list[tuple[str, int, datetime, datetime]]] = {}
    family_of: dict[str, str] = {}
    for family, template, n, first, last in family_rows:
        by_family.setdefault(str(family), []).append((str(template), int(n), first, last))
        family_of[str(template)] = str(family)
    notes: dict[str, str] = {}
    for template in series:
        family = family_of.get(template)
        partners = [p for p in by_family.get(family or "", []) if p[0] != template]
        if family is None or not partners:
            continue
        named = "; ".join(
            f"{p} ({n:,} Log Entries, {first.isoformat()} to {last.isoformat()})"
            for p, n, first, last in partners
        )
        notes[template] = (
            f"Pattern {template} shares Pattern Family {family} with {named}: the wording changed, so a "
            "drop or end of this series can be the hand-off to that Pattern, not the problem ending. "
            f"trend or count_by with family_id='{family}' counts the family as one."
        )
    return notes


def bucket_statement(part: Part, keys: Sequence[str], bucket: str) -> Q:
    """Zero-filled counts per bucket and series: a generate_series grid crossed with the series."""
    interval = BUCKETS[bucket][0]
    span = part.window
    return q(
        """WITH matching AS (
    SELECT {} AS k, {} AS ts FROM {} WHERE {} AND {} = ANY({})
),
grid AS (
    SELECT unnest(generate_series(
        time_bucket({}::INTERVAL, {}::TIMESTAMP), {}::TIMESTAMP, {}::INTERVAL
    )) AS bucket
),
series AS (SELECT unnest({}) AS k),
counted AS (
    SELECT time_bucket({}::INTERVAL, ts) AS bucket, k, count(*) AS n FROM matching GROUP BY bucket, k
)
SELECT grid.bucket, series.k, coalesce(counted.n, 0) AS n
FROM grid CROSS JOIN series
LEFT JOIN counted ON counted.bucket = grid.bucket AND counted.k = series.k
ORDER BY grid.bucket, series.k""",
        part.key,
        part.col("time"),
        part.source,
        part.conditions(in_window=True),
        part.key,
        bound(list(keys), "::VARCHAR[]"),
        bound(interval),
        bound(span.start),
        bound(span.end - MICROSECOND),
        bound(interval),
        bound(list(keys), "::VARCHAR[]"),
        bound(interval),
    )


def family_statement(part: Part, template_ids: Sequence[str]) -> Q:
    """Every Pattern of the Dataset that shares a Pattern Family with one of `template_ids`."""
    rel = part.rel
    family, template, ts = rel.column("family_id"), rel.column("pattern"), rel.column("time")
    return q(
        """SELECT e.{}, e.{}, count(*) AS n, min(e.{}) AS first_seen, max(e.{}) AS last_seen
FROM {} AS e
WHERE e.{} IN (SELECT f.{} FROM {} AS f WHERE f.{} = ANY({}) AND f.{} IS NOT NULL)
GROUP BY e.{}, e.{}
ORDER BY e.{}, e.{}""",
        family,
        template,
        ts,
        ts,
        rel.table,
        family,
        family,
        rel.table,
        template,
        bound(list(template_ids), "::VARCHAR[]"),
        family,
        family,
        template,
        family,
        template,
    )


def register(server: MCPServer) -> None:
    """Register `trend` on `server`."""

    @evidence_tool(server, title="Trend of Log Entries per time bucket, with baselines")
    def trend(
        role: RoleParam,
        dataset: Annotated[
            DatasetName,
            Field(
                description="'bgl' or 'openstack'. One Dataset per trend: their time ranges are years apart."
            ),
        ],
        bucket: Annotated[
            Bucket,
            Field(
                description="Bucket width: minute, hour, day or week (Monday-start weeks); at most "
                f"{MAX_BUCKETS} buckets."
            ),
        ] = "day",
        group_by: Annotated[
            TrendGroupBy | None,
            Field(
                description="Optional dimension for one series per group: component, team, level, node, "
                "rack, midplane, alert_category (these four BGL only) or pattern. Omit it for one series."
            ),
        ] = None,
        query: QueryParam = None,
        time_from: TimeFromParam = None,
        time_to: TimeToParam = None,
        components: ComponentsParam = None,
        levels: LevelsParam = None,
        sources: SourcesParam = None,
        alert_categories: AlertCategoriesParam = None,
        pattern_ids: PatternIdsParam = None,
        family_id: FamilyIdParam = None,
        top: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_SERIES,
                description="With group_by: how many of the largest groups to draw as series "
                f"(at most {MAX_SERIES}).",
            ),
        ] = MAX_SERIES,
    ) -> ToolOutcome:
        """Zero-filled counts of matching Log Entries per time bucket, optionally one series per group.

        Purpose: show whether a problem is rising, steady, a one-off burst or gone, over a stated
        period of one Dataset. Counts are Log Entries, not Incidents.
        Quote exact numbers, never round, and quote each series' count, peak and baseline instead
        of adding buckets yourself. Use count_by for totals per group and search_logs for example
        Log Entries. With
        group_by='pattern', a series whose Pattern shares a Pattern Family with another Pattern
        carries a hand-off note, so a wording change is not read as the Pattern ending; until
        Pattern Families are assigned in the database, no such note appears.
        Inputs: role, dataset, bucket, and optional group_by, query (all words; "quoted phrase"),
        time_from, time_to, components, levels, sources, alert_categories, pattern_ids, family_id
        and top. More than 400 buckets is an error that names a bucket that fits.
        Outputs: the Evidence envelope. rows: one per bucket, columns bucket and one column per
        series ('(all)' without group_by). summary: bucket, buckets, total, groups_total,
        series_total, other_count, and per series its count, share_pct, peak_bucket, peak_count,
        prev_period, median_week, expected, baseline_flag and any handoff. notes: the baseline
        method. total_matches counts buckets, not Log Entries.
        """
        datasets, notes = choose_datasets(dataset, group_by, alert_categories)
        prepared = prepare(
            role,
            datasets,
            group_by=group_by,
            query=query,
            time_from=time_from,
            time_to=time_to,
            components=components,
            levels=levels,
            sources=sources,
            alert_categories=alert_categories,
            pattern_ids=pattern_ids,
            family_id=family_id,
            notes=notes,
        )
        return _trend(prepared, bucket, top)


def _trend(prepared: Prepared, bucket: str, top: int) -> ToolOutcome:
    part = prepared.parts[0]
    span = part.window
    if span.end <= span.start:
        raise ScopeError(
            f"The time range does not overlap the {part.dataset} Log Entries, which run from "
            f"{span.first.isoformat()} to {span.dataset_end.isoformat()}. Pass time_from and time_to "
            "inside that range, or call describe_corpus for the Datasets' time ranges."
        )
    check_buckets(part.dataset, span.start, span.end, bucket)
    collected = Collected()
    group_by = prepared.group_by
    ranked: Ranked | None = None
    keys = [ALL]
    if group_by is not None:
        ranked = read_ranked(
            collected.add(run(rank_statement([part], top, show_single_hidden=False), prepared.role))
        )
        if ranked.groups:
            keys = ranked.shown(part.dataset)
        else:  # nothing matched: one zero series instead of none
            part = prepared.regrouped(None)[0]
    counted = collected.add(run(bucket_statement(part, keys, bucket), prepared.role))
    labels, columns = _pivot(counted.rows, keys, bucket)
    stats = read_stats(
        collected.add(run(stats_statement([part], {part.dataset: keys}, {part.dataset: keys}), prepared.role))
    )
    handoffs: dict[str, str] = {}
    patterns = [k for k in keys if k != NULL_LABELS["pattern"]] if group_by == "pattern" and ranked else []
    if patterns:
        family = collected.add(run(family_statement(part, patterns), prepared.role))
        handoffs = handoff_notes(patterns, family.rows)

    rows = [[label, *(columns[k][i] for k in keys)] for i, label in enumerate(labels)]
    series_total = sum(sum(columns[k]) for k in keys)
    # Grouped: the ranking counted every group, shown or not. Ungrouped: the one series is everything.
    total = ranked.total if ranked is not None else series_total
    groups_total = ranked.groups_total if ranked is not None else 1
    entries = []
    for k in keys:
        values = columns[k]
        count = sum(values)
        row_stats = stats[(part.dataset, k)]
        if row_stats.count != count:
            raise RuntimeError(f"trend statements disagree on {k!r}: {count} against {row_stats.count}")
        peak = max(range(len(values)), key=lambda i: values[i]) if values else None
        base = baseline_of(count, row_stats, span)
        entry: dict[str, Any] = {
            "series": k,
            "count": count,
            "share_pct": share(count, total),
            "peak_bucket": labels[peak] if peak is not None else None,
            "peak_count": values[peak] if peak is not None else 0,
            "prev_period": base.prev_period,
            "median_week": base.median_week,
            "expected": base.expected,
            "baseline_flag": base.flag,
        }
        if k in handoffs:
            entry["handoff"] = handoffs[k]
        entries.append(entry)

    weeks = whole_weeks(stats, [part])
    notes = [*common_notes(prepared, weeks), f"Buckets follow ts: {part.rel.dataset.time_note}"]
    if group_by is not None and groups_total > len(keys):
        notes.append(
            f"Showing the {len(keys)} largest of {groups_total:,} groups as series; other_count = "
            f"{total - series_total:,} Log Entries are in the other {groups_total - len(keys):,} groups."
        )
    if ranked is not None:
        notes.extend(unassigned_pattern_notes(ranked, group_by))
    notes.extend(handoffs[k] for k in keys if k in handoffs)
    if total == 0:
        notes.append(NO_MATCH_NOTE)
    summary = {
        "bucket": bucket,
        "buckets": len(rows),
        "group_by": group_by,
        "total": total,
        "groups_total": groups_total,
        "series_total": series_total,
        "other_count": total - series_total,
        "series": entries,
        "baseline": baseline_summary([part], weeks),
    }
    return ToolOutcome(
        result=collected.combined(total=len(rows), rows=rows),
        scope=prepared.scope,
        columns=["bucket", *keys],
        summary=summary,
        notes=notes,
        rows=rows,
        total_matches=len(rows),
    )


def _pivot(
    rows: Sequence[Sequence[Any]], keys: Sequence[str], bucket: str
) -> tuple[list[str], dict[str, list[int]]]:
    """Long rows (bucket, series, n) in bucket order -> bucket labels and one count list per series."""
    labels: list[str] = []
    columns: dict[str, list[int]] = {k: [] for k in keys}
    last: Any = None
    for start, key, n in rows:
        if start != last:
            labels.append(bucket_label(start, bucket))
            last = start
        columns[str(key)].append(int(n))
    return labels, columns
