"""`top_patterns`: Patterns ranked within a Data Scope, with Incidents, Pareto share, Chronic and New
Pattern flags, drift hand-offs and the Manager's priority band (T28, #4).

A Pattern here is `pattern_key`, the key Incidents are built on (#1): the alert category for a BGL
alert line, the template id otherwise. Per Dataset, one statement (`measure_statement`) measures every
Pattern in the Data Scope and ranks them; a second (`catalogue_statement`) reads the Pattern catalogue
for the shown template ids: their template text and the Patterns of their Pattern Family, from which
`handoffs` finds the wording drifts.

Incidents are chained at query time over the Data Scope, with the default gap of the Incident table
(300 s on `epoch_utc`, merged across sources), so they always match the filters; an Incident that
crosses the Data Scope's edge is cut there.

The priority ranking (#4 Decision proposal, `sort='priority'`) runs over a stated candidate filter,
BGL alert categories plus OpenStack Request Failure Patterns, and is the equal-weight mean of five
percentile ranks (average rank for ties, as pandas' `rank(pct=True)`): Incidents, Spread (distinct
midplanes; instances for OpenStack), active weeks, the Incident rate of the last 28 days of the
Data Scope against its whole-period rate, and recency (whole days since the last Log Entry). Its top 5
is a band, not an order: in #4 the band was stable under 2,000 random weightings, the order inside it
was not.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp.corpus.facts import dataset_facts
from serenedb_log_mcp.evidence import RoleParam, ScopeFilters
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import (
    AlertCategoriesParam,
    Collected,
    ComponentsParam,
    DatasetName,
    FamilyIdParam,
    LevelsParam,
    Part,
    PatternIdsParam,
    Q,
    QueryParam,
    SourcesParam,
    UtcTimeFromParam,
    UtcTimeToParam,
    bound,
    choose_datasets,
    iso,
    joined,
    prepare,
    q,
    run,
    share,
)
from serenedb_log_mcp.tools._catalogue import shape_text

Sort = Literal["entries", "incidents", "sources", "recent", "priority"]

DEFAULT_TOP = 20
MAX_TOP = 50
GAP_S = 300
"""The gap of the default Incident table (T10): a longer silence starts a new Incident."""
WEEK_S = 7 * 86_400
DAY_S = 86_400
TREND_DAYS = 28
"""The priority's trend factor compares the Incidents of the last 28 days with the whole period (#4)."""
BAND = 5
"""The Manager's band: the top 5 by priority, read as a set."""
CHRONIC_MIN_WEEKS = 4
"""A Data Scope shorter than this many weeks marks no Pattern Chronic."""
NEW_DAYS = 7
"""A New Pattern's first Log Entry lies within this many days before the end of the Data Scope..."""
NEW_SHARE = 0.25
"""...or within this share of the Data Scope's length, whichever is shorter..."""
NEW_MIN_ENTRIES = 10
"""...and it has at least this many Log Entries in the Data Scope."""
DRIFT_WINDOW = timedelta(days=7)
"""A Pattern hands off to a Pattern of its Family that starts within this long of its last Log Entry.

On BGL the "over N seconds" wordings start on 2005-11-15 while the old wordings run on until
2005-11-21: the two overlap by about six days, so the window is a week, not a day."""
REQUEST_FAILURE_LEVELS = ("ERROR", "CRITICAL")

ALERT = "alert_category"
TEMPLATE = "template"

CANDIDATE_FILTER = (
    "BGL alert categories (alert lines only) plus OpenStack Request Failure Patterns (Log Entries at "
    "level ERROR or CRITICAL, or with an HTTP status of 500 or more)"
)
CHRONIC_RULE = (
    f"chronic: the Data Scope spans at least {CHRONIC_MIN_WEEKS} weeks, the Pattern has Log Entries in "
    "more than half of them, and no single week holds half or more of its Log Entries (a steady rate)."
)
NEW_RULE = (
    f"new: the Pattern's first Log Entry up to the end of the Data Scope lies within the last {NEW_DAYS} "
    f"days of the Data Scope, or its last {NEW_SHARE:.0%} when that is shorter, and it has at least "
    f"{NEW_MIN_ENTRIES} Log Entries in the Data Scope."
)
DRIFT_RULE = (
    "handoff_to and handoff_from: another Pattern of the same Pattern Family (family_id, a wording "
    f"drift) starts within {DRIFT_WINDOW.days} days before or after this Pattern's last Log Entry and "
    "outlives it (handoff_to), or ended within that long of this Pattern's first Log Entry after starting "
    "before it (handoff_from), over the whole Dataset. Such an end is the "
    "wording changing, not the problem ending: count_by or trend with family_id counts the family as one."
)
INCIDENTS_NOTE = (
    f"Incidents are chained at query time over this Data Scope: Log Entries of one Pattern, merged across "
    f"sources, where each follows the previous one within {GAP_S} s on epoch_utc (the default of the "
    "Incident table). An Incident that crosses the edge of the Data Scope is cut there. entries counts "
    "Log Entries; incidents counts Incidents; never call Log Entries Incidents."
)
PARETO_NOTE = (
    "share_pct is this Pattern's share of the Log Entries in the Data Scope; pareto_pct is the cumulative "
    "share of this Pattern and every Pattern with more Log Entries in the same Dataset, so a high "
    "pareto_pct on a high-priority Pattern means it is rare in lines but not in Incidents."
)
SPREAD_NOTE = "spread counts distinct BGL midplanes and OpenStack instances (instance_ref)."
PRIORITY_NOTE = (
    "priority is the equal-weight mean of five percentile ranks within each Dataset's candidates "
    "(ties get their average rank): p_incidents, p_spread (midplanes; instances for OpenStack), "
    f"p_active_weeks, p_trend (Incidents of the last {TREND_DAYS} days of the Data Scope against the "
    "whole-period rate, incident_rate_ratio) and p_recency (fewer whole days since the last Log Entry "
    f"rank higher). The top {BAND} per Dataset form the band; read it as a set, not an order, because "
    "the band was stable under 2,000 random weightings of the factors in #4 and the order inside it was "
    "not."
)
SHORT_TREND_NOTE = (
    "The Data Scope of {datasets} is shorter than {days} days, so the trend window is the whole Data "
    "Scope: incident_rate_ratio is 1 for every Pattern and p_trend does not separate them."
)


@dataclass(frozen=True)
class Plan:
    """What one Dataset's measure statement needs beyond its Part."""

    part: Part
    start_ep: float
    end_ep: float
    span_days: float
    trend_days: float
    new_days: float
    candidates_only: bool

    @property
    def trend_from_ep(self) -> float:
        return self.end_ep - self.trend_days * DAY_S

    @property
    def weeks(self) -> int:
        """The Data Scope's weeks, counted from its start; a partial last week counts."""
        return max(1, math.ceil(self.span_days / 7))


def epoch(value: datetime, zone: str) -> float:
    """A `ts` value as logged in `zone`, as UTC epoch seconds."""
    return value.replace(tzinfo=ZoneInfo(zone)).timestamp()


def plan(part: Part, candidates_only: bool) -> Plan:
    span = part.window
    zone = part.rel.dataset.ts_zone
    start_ep, end_ep = epoch(span.start, zone), epoch(span.end, zone)
    span_days = max((end_ep - start_ep) / DAY_S, 1 / DAY_S)
    return Plan(
        part=part,
        start_ep=start_ep,
        end_ep=end_ep,
        span_days=span_days,
        trend_days=min(TREND_DAYS, span_days),
        new_days=min(NEW_DAYS, NEW_SHARE * span_days),
        candidates_only=candidates_only,
    )


def candidate_condition(part: Part) -> Q:
    """The priority's candidate filter: BGL alert lines, OpenStack Request Failure Log Entries."""
    if part.dataset == "bgl":
        return q("{} IS NOT NULL", part.col("alert_category"))
    return q(
        "({} = ANY({}) OR {} >= 500)",
        part.col("level"),
        bound(list(REQUEST_FAILURE_LEVELS), "::VARCHAR[]"),
        part.col("http_status"),
    )


def _pct(column: str, *, descending: bool = False) -> sql.Composable:
    """Average-rank percentile of `column` among all rows: (min rank + max rank) / 2 / n."""
    order = sql.SQL("{} DESC" if descending else "{}").format(sql.Identifier(column))
    return sql.SQL(
        "(CAST(rank() OVER (ORDER BY {o}) AS DOUBLE) / count(*) OVER () "
        "+ cume_dist() OVER (ORDER BY {o})) / 2"
    ).format(o=order)


SORT_ORDER: Mapping[str, str] = {
    "entries": "entries DESC, k",
    "incidents": "incidents DESC, entries DESC, k",
    "sources": "sources DESC, entries DESC, k",
    "recent": "last_ep DESC, entries DESC, k",
    "priority": "priority DESC, incidents DESC, k",
}

MEASURE_COLUMNS = (
    "k",
    "component",
    "entries",
    "incidents",
    "sources",
    "spread",
    "first_seen",
    "last_seen",
    "last_ep",
    "first_ever_ep",
    "active_weeks",
    "peak_week",
    "recent_incidents",
    "incident_rate_ratio",
    "days_since_last",
    "patterns",
    "entries_total",
    "incidents_total",
    "cum_entries",
    "p_incidents",
    "p_spread",
    "p_active_weeks",
    "p_trend",
    "p_recency",
    "priority",
)


def measure_statement(p: Plan, sort: str, top: int) -> Q:
    """Every Pattern of one Dataset in the Data Scope, measured and ranked; the top `top` returned."""
    part = p.part
    window = part.window
    conditions = [part.conditions(in_window=False), q("{} IS NOT NULL", part.col("pattern_key"))]
    conditions.append(q("{} < {}", part.col("time"), bound(window.end)))
    if p.candidates_only:
        conditions.append(candidate_condition(part))
    spread = part.col("midplane") if part.dataset == "bgl" else part.col("instance")
    return q(
        """WITH matching AS (
    SELECT {} AS k, {} AS id, {} AS ep, {} AS ts, {} AS src, {} AS spread, {} AS component,
           ({}) AS in_scope
    FROM {} WHERE {}
),
scoped AS (SELECT * FROM matching WHERE in_scope),
chained AS (
    SELECT k, ep,
           CASE WHEN lag(ep) OVER (PARTITION BY k ORDER BY ep, id) IS NULL
                  OR ep - lag(ep) OVER (PARTITION BY k ORDER BY ep, id) > {} THEN 1 ELSE 0 END AS starts
    FROM scoped
),
chains AS (
    SELECT k, sum(starts) AS incidents,
           sum(CASE WHEN starts = 1 AND ep >= {} THEN 1 ELSE 0 END) AS recent_incidents
    FROM chained GROUP BY k
),
weekly AS (
    SELECT k, CAST(floor((ep - {}) / {}) AS BIGINT) AS week, count(*) AS n FROM scoped GROUP BY k, week
),
weeks AS (SELECT k, count(*) AS active_weeks, max(n) AS peak_week FROM weekly GROUP BY k),
counted AS (
    SELECT k, count(*) AS entries, count(DISTINCT src) AS sources, count(DISTINCT spread) AS spread,
           mode(component) AS component, min(ts) AS first_seen, max(ts) AS last_seen, max(ep) AS last_ep
    FROM scoped GROUP BY k
),
ever AS (SELECT k, min(ep) AS first_ever_ep FROM matching GROUP BY k),
measured AS (
    SELECT c.k, c.component, c.entries, ch.incidents, c.sources, c.spread, c.first_seen, c.last_seen,
           c.last_ep, ev.first_ever_ep, w.active_weeks, w.peak_week, ch.recent_incidents,
           CAST(ch.recent_incidents AS DOUBLE) / (ch.incidents * {}) AS incident_rate_ratio,
           CAST(floor(({} - c.last_ep) / {}) AS BIGINT) AS days_since_last
    FROM counted c
    JOIN chains ch ON ch.k = c.k
    JOIN weeks w ON w.k = c.k
    JOIN ever ev ON ev.k = c.k
),
ranked AS (
    SELECT *,
           count(*) OVER () AS patterns,
           sum(entries) OVER () AS entries_total,
           sum(incidents) OVER () AS incidents_total,
           sum(entries) OVER (ORDER BY entries DESC, k ROWS UNBOUNDED PRECEDING) AS cum_entries,
           {} AS p_incidents,
           {} AS p_spread,
           {} AS p_active_weeks,
           {} AS p_trend,
           {} AS p_recency
    FROM measured
),
scored AS (
    SELECT *, (p_incidents + p_spread + p_active_weeks + p_trend + p_recency) / 5 AS priority FROM ranked
)
SELECT {} FROM scored ORDER BY {} LIMIT {}""",
        part.col("pattern_key"),
        part.col("entry_id"),
        part.col("epoch"),
        part.col("time"),
        part.col("source"),
        spread,
        part.col("component"),
        part.between(window.start, window.end),
        part.source,
        joined(" AND ", conditions),
        bound(GAP_S),
        bound(p.trend_from_ep),
        bound(p.start_ep),
        bound(WEEK_S),
        bound(p.trend_days / p.span_days),
        bound(p.end_ep),
        bound(DAY_S),
        _pct("incidents"),
        _pct("spread"),
        _pct("active_weeks"),
        _pct("incident_rate_ratio"),
        _pct("days_since_last", descending=True),
        sql.SQL(", ").join(sql.Identifier(c) for c in MEASURE_COLUMNS),
        sql.SQL(SORT_ORDER[sort]),
        bound(top),
    )


def catalogue_statement(part: Part, template_ids: Sequence[str]) -> Q:
    """The catalogue rows of `template_ids`, each with every other Pattern of its Pattern Family.

    Times are first_epoch and last_epoch (UTC seconds, #64), never the local first_seen/last_seen;
    display_text is the label a ranking shows, '(no message)' for the empty Pattern."""
    patterns = part.rel.patterns_table
    return q(
        """SELECT p.template_id, p.display_text, p.first_epoch, p.last_epoch,
       f.template_id AS partner, f.first_epoch AS partner_first, f.last_epoch AS partner_last
FROM {} AS p
LEFT JOIN {} AS f ON f.dataset = p.dataset AND f.family_id = p.family_id AND f.template_id <> p.template_id
WHERE p.dataset = {} AND p.template_id = ANY({})
ORDER BY p.template_id, f.first_epoch, f.template_id""",
        patterns,
        patterns,
        bound(part.dataset),
        bound(list(template_ids), "::VARCHAR[]"),
    )


@dataclass(frozen=True)
class CatalogueEntry:
    """One shown template: its label, its UTC span over the whole Dataset and its Family partners."""

    text: str | None
    first_epoch: float | None
    last_epoch: float | None
    partners: tuple[tuple[str, float, float], ...]


def read_catalogue(rows: Sequence[Sequence[Any]]) -> dict[str, CatalogueEntry]:
    found: dict[str, CatalogueEntry] = {}
    for template, text, first, last, partner, partner_first, partner_last in rows:
        key = str(template)
        entry = found.get(key) or CatalogueEntry(shape_text(text), _float(first), _float(last), ())
        if partner is not None and partner_first is not None and partner_last is not None:
            partners = (*entry.partners, (str(partner), float(partner_first), float(partner_last)))
            entry = CatalogueEntry(entry.text, entry.first_epoch, entry.last_epoch, partners)
        found[key] = entry
    return found


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def handoffs(entry: CatalogueEntry) -> tuple[str | None, str | None]:
    """(handoff_to, handoff_from): the Family partners that start as this Pattern ends and outlive it,
    and those that started earlier and end as this Pattern starts, each within DRIFT_WINDOW (#1,
    template drift). The two wordings may overlap: the new one can start before the old one ends."""
    if entry.first_epoch is None or entry.last_epoch is None:
        return None, None
    window = DRIFT_WINDOW.total_seconds()
    to = [
        p
        for p, first, last in entry.partners
        if abs(first - entry.last_epoch) <= window and first > entry.first_epoch and last > entry.last_epoch
    ]
    since = [
        p
        for p, first, last in entry.partners
        if abs(last - entry.first_epoch) <= window and first < entry.first_epoch and last < entry.last_epoch
    ]
    return (", ".join(to) or None), (", ".join(since) or None)


def is_chronic(*, weeks: int, active_weeks: int, peak_week: int, entries: int) -> bool:
    """CHRONIC_RULE."""
    return weeks >= CHRONIC_MIN_WEEKS and 2 * active_weeks > weeks and 2 * peak_week < entries


def is_new(*, first_ever_ep: float, end_ep: float, new_days: float, entries: int) -> bool:
    """NEW_RULE."""
    return first_ever_ep >= end_ep - new_days * DAY_S and entries >= NEW_MIN_ENTRIES


BASE_COLUMNS = (
    "dataset",
    "pattern",
    "kind",
    "template_text",
    "component",
    "entries",
    "share_pct",
    "pareto_pct",
    "incidents",
    "sources",
    "spread",
    "first_seen",
    "last_seen",
    "active_weeks",
    "chronic",
    "new",
    "handoff_to",
    "handoff_from",
)
PRIORITY_COLUMNS = (
    "band",
    "priority",
    "p_incidents",
    "p_spread",
    "p_active_weeks",
    "p_trend",
    "p_recency",
    "recent_incidents",
    "incident_rate_ratio",
    "days_since_last",
)


def _round(value: Any) -> float | None:
    return None if value is None else round(float(value), 4)


def register(server: MCPServer) -> None:
    """Register `top_patterns` on `server`."""

    @evidence_tool(server, title="Rank Patterns by Log Entries, Incidents, sources, recency or priority")
    def top_patterns(
        role: RoleParam,
        sort: Annotated[
            Sort,
            Field(
                description="How to rank: 'entries' (Log Entries, the Pareto view), 'incidents', "
                "'sources' (distinct nodes or instances), 'recent' (latest last Log Entry) or 'priority' "
                "(the Manager ranking over BGL alert categories plus OpenStack Request Failure "
                "Patterns, with a top-5 band)."
            ),
        ] = "entries",
        dataset: Annotated[
            DatasetName | None,
            Field(description="'bgl' or 'openstack'; omit it for both, ranked per Dataset."),
        ] = None,
        top: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_TOP,
                description=f"How many Patterns to list per Dataset (default {DEFAULT_TOP}, at most "
                f"{MAX_TOP}).",
            ),
        ] = DEFAULT_TOP,
        query: QueryParam = None,
        time_from: UtcTimeFromParam = None,
        time_to: UtcTimeToParam = None,
        components: ComponentsParam = None,
        levels: LevelsParam = None,
        sources: SourcesParam = None,
        alert_categories: AlertCategoriesParam = None,
        pattern_ids: PatternIdsParam = None,
        family_id: FamilyIdParam = None,
    ) -> ToolOutcome:
        """Patterns ranked within a Data Scope, with Log Entries, Incidents, Spread and flags.

        Purpose: rank recurring problems. Use top_patterns to rank Patterns by frequency or
        priority in a Data Scope, and list_patterns to choose Patterns for a concept. A Pattern is a
        BGL alert category for alert lines and a template id otherwise. sort='priority' is the
        Manager ranking: over BGL alert categories plus OpenStack Request Failure Patterns only, the
        equal-weight mean of percentile ranks of Incidents, Spread, active weeks, the 28-day
        Incident trend and recency; the top 5 per Dataset are a band (band=true), to be read as a
        set, not an order. Counts are exact; quote them as given, never round, and never call Log
        Entries Incidents: entries and incidents are separate columns.
        Inputs: role, sort (entries, incidents, sources, recent, priority), and optional dataset,
        top, query, time_from, time_to, components, levels, sources, alert_categories, pattern_ids
        and family_id.
        Outputs: the Evidence envelope. rows: one per Pattern with dataset, pattern, kind
        (alert_category or template), template_text, component, entries, share_pct, pareto_pct
        (cumulative share of Log Entries), incidents, sources, spread (midplanes; instances for
        OpenStack), first_seen, last_seen, active_weeks, chronic, new, handoff_to and handoff_from
        (a wording drift within the Pattern Family); with sort='priority' also band, priority and
        the five factor percentiles. summary: per Dataset entries_total, incidents_total and
        patterns_total. notes: the rules behind every flag. total_matches counts Patterns.
        """
        datasets, notes = choose_datasets(dataset, None, alert_categories)
        prepared = prepare(
            role,
            datasets,
            group_by=None,
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
        priority = sort == "priority"
        scope = prepared.scope
        if priority:
            filters = {**scope.filters.model_dump(exclude_none=True), "candidates": CANDIDATE_FILTER}
            scope = scope.model_copy(update={"filters": ScopeFilters.of(**filters)})
        collected = Collected()
        rows: list[list[Any]] = []
        summary: dict[str, Any] = {"sort": sort, "top_per_dataset": top, "datasets": {}}
        total_patterns = 0
        short_trend: list[str] = []
        empty: list[str] = []
        for part in prepared.parts:
            current = plan(part, priority)
            if priority and current.span_days < TREND_DAYS:
                short_trend.append(part.dataset)
            measured = collected.add(run(measure_statement(current, sort, top), prepared.role))
            records = [dict(zip(MEASURE_COLUMNS, row, strict=True)) for row in measured.rows]
            alerts = set(dataset_facts(part.dataset, prepared.role).alert_categories)
            templates = [str(r["k"]) for r in records if str(r["k"]) not in alerts]
            catalogue: dict[str, CatalogueEntry] = {}
            if templates:
                found = collected.add(run(catalogue_statement(part, templates), prepared.role))
                catalogue = read_catalogue(found.rows)
            patterns = int(records[0]["patterns"]) if records else 0
            entries_total = int(records[0]["entries_total"]) if records else 0
            incidents_total = int(records[0]["incidents_total"]) if records else 0
            total_patterns += patterns
            if not records:
                empty.append(part.dataset)
            summary["datasets"][part.dataset] = {
                "entries_total": entries_total,
                "incidents_total": incidents_total,
                "patterns_total": patterns,
                "weeks": current.weeks,
                "shown": len(records),
            }
            for rank, r in enumerate(records, start=1):
                rows.append(_row(part.dataset, r, rank, current, alerts, catalogue, priority))
        summary["patterns_total"] = total_patterns
        notes_out = [INCIDENTS_NOTE, PARETO_NOTE, SPREAD_NOTE, CHRONIC_RULE, NEW_RULE, DRIFT_RULE]
        if priority:
            notes_out += [f"Priority candidates: {CANDIDATE_FILTER}.", PRIORITY_NOTE]
            if short_trend:
                notes_out.append(SHORT_TREND_NOTE.format(datasets=", ".join(short_trend), days=TREND_DAYS))
        notes_out += list(prepared.notes)
        if empty:
            notes_out.append(
                f"No Pattern in {', '.join(empty)} matched this Data Scope"
                + (" and the priority candidate filter" if priority else "")
                + ". Widen the time range or drop a filter."
            )
        columns = [*BASE_COLUMNS, *(PRIORITY_COLUMNS if priority else ())]
        shown = len(rows)
        return ToolOutcome(
            result=collected.combined(total=total_patterns, rows=rows),
            scope=scope,
            columns=columns,
            summary=summary,
            notes=notes_out,
            rows=rows,
            total_matches=total_patterns,
            truncation_note=(
                f"Showing {shown} of {total_patterns:,} Patterns (the top {top} per Dataset by {sort}); "
                "entries_total and incidents_total in summary cover every Pattern, so they are exact."
            ),
        )


def _row(
    dataset: str,
    r: Mapping[str, Any],
    rank: int,
    p: Plan,
    alerts: set[str],
    catalogue: Mapping[str, CatalogueEntry],
    priority: bool,
) -> list[Any]:
    key = str(r["k"])
    kind = ALERT if key in alerts else TEMPLATE
    entry = catalogue.get(key)
    handoff_to, handoff_from = handoffs(entry) if entry else (None, None)
    entries = int(r["entries"])
    total = int(r["entries_total"])
    row: list[Any] = [
        dataset,
        key,
        kind,
        entry.text if entry else None,
        r["component"],
        entries,
        share(entries, total),
        share(int(r["cum_entries"]), total),
        int(r["incidents"]),
        int(r["sources"]),
        int(r["spread"]),
        iso(r["first_seen"]),
        iso(r["last_seen"]),
        int(r["active_weeks"]),
        is_chronic(
            weeks=p.weeks, active_weeks=int(r["active_weeks"]), peak_week=int(r["peak_week"]), entries=entries
        ),
        is_new(
            first_ever_ep=float(r["first_ever_ep"]), end_ep=p.end_ep, new_days=p.new_days, entries=entries
        ),
        handoff_to,
        handoff_from,
    ]
    if priority:
        row += [
            rank <= BAND,
            _round(r["priority"]),
            _round(r["p_incidents"]),
            _round(r["p_spread"]),
            _round(r["p_active_weeks"]),
            _round(r["p_trend"]),
            _round(r["p_recency"]),
            int(r["recent_incidents"]),
            _round(r["incident_rate_ratio"]),
            int(r["days_since_last"]),
        ]
    return row
