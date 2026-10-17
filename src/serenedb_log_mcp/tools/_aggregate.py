"""Shared SQL and shaping for the aggregation tools `count_by` and `trend` (T6).

Both tools count Log Entries per group of one dimension over a Data Scope and put a baseline next
to every count (`baseline.py`). The SQL comes from fixed templates: table, index and column names
come from `corpus.datasets.relation()`, every value a caller passes is a bound parameter, and the
only literals formatted in are Dataset names and group labels defined in this module.

Statements (each a read-only `db.fetch_all`; the Evidence holds all of them, in order):

- `rank_statement`: the groups in the Data Scope ranked by count, plus one roll-up row per Dataset
  with its total and its number of groups;
- `stats_statement`: per shown group, and per Dataset for the `(other)` row, the count, distinct
  sources, first and last seen, the previous-period count and the median week;
- `bucket_statement` (trend): zero-filled counts per time bucket and series, with `time_bucket` and
  `generate_series`;
- `family_statement` (trend by Pattern): the Patterns that share a Pattern Family with a series.

On SereneDB 26.10.1 the table-function form `FROM generate_series(%s, %s, ...)` fails with bound
timestamps ("Invalid unicode (byte sequence mismatch)"), and so does a `VALUES` list of bound strings
next to a bound `generate_series`. The templates therefore use `unnest(generate_series(...))` and
`unnest(%s::VARCHAR[])`, which work, and `= ANY(%s::VARCHAR[])` for lists (a bare `= ANY(%s)` fails).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, get_args

from psycopg import sql
from pydantic import Field

from serenedb_log_mcp import fts
from serenedb_log_mcp.baseline import MIN_WEEKS, DatasetWindow, few_weeks_notes, judge, method_note, window
from serenedb_log_mcp.clock import UTC_ZONE, clock_note, time_columns, utc_input, utc_range, utc_time
from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import DATASET_NAMES, OWNERSHIP_NOTE, DatasetName, Relation, relation
from serenedb_log_mcp.corpus.facts import DatasetFacts, dataset_facts
from serenedb_log_mcp.db import QueryResult, fetch_all
from serenedb_log_mcp.evidence import DataScope
from serenedb_log_mcp.scope import ScopeError, build_scope, resolve_time, time_bounds, validate_choices

GroupBy = Literal[
    "dataset", "component", "team", "level", "node", "rack", "midplane", "alert_category", "pattern"
]
GROUP_BYS: tuple[str, ...] = get_args(GroupBy)
BGL_ONLY_GROUPS = ("node", "rack", "midplane", "alert_category")

OTHER = "(other)"
"""Label of the row that holds the groups a top-N result does not show."""
ALL = "(all)"
"""Label of the single series of an ungrouped trend."""
NULL_LABELS: Mapping[str, str] = {
    "component": "(no component)",
    "level": "(no level)",
    "node": "(no node)",
    "rack": "(no rack)",
    "midplane": "(no midplane)",
    "alert_category": "(non-alert)",
    "pattern": "(no Pattern)",
    "team": "(unmapped)",
}
"""Labels for Log Entries without a value in the grouped column (team: a Component the Ownership
Table does not map)."""

LOG_ENTRIES_NOTE = (
    "Every count here is Log Entries, not Incidents: one failing node can write thousands of lines "
    "about a single problem, so a large count is not many failures. Quote the numbers exactly as "
    "given; never round them."
)
SOURCES_NOTE = (
    "distinct_sources counts BGL nodes and OpenStack instances (instance_ref); OpenStack Log Entries "
    "that name no instance are not counted as a source."
)
NO_MATCH_NOTE = (
    "No Log Entries matched this Data Scope. Widen the time range, drop a filter, or check the query "
    "words and filter values with describe_corpus."
)
AGGREGATION_CLOCK_LEAD = (
    "Every time here is UTC: time_from, time_to, the buckets, first_seen, last_seen and the "
    "baseline periods, and time inputs are read as UTC (an input with an offset is converted)."
)
"""The first sentence of the clock note (`clock.clock_note`) of count_by and trend."""
AGGREGATION_ON_EPOCH = "time range, buckets and baselines"
"""What count_by and trend compute on epoch_utc for a Dataset whose ts is local time."""

# Tool parameters shared by count_by and trend -----------------------------------------------------------

QueryParam = Annotated[
    str | None,
    Field(
        description="Optional full-text filter on the message: every word must appear, in any order; "
        "put a phrase in double quotes, for example '\"data TLB error\"'. Omit it to count every Log "
        "Entry in the Data Scope.",
        max_length=fts.MAX_QUERY_CHARS,
    ),
]
UtcTimeFromParam = Annotated[
    str | None,
    Field(
        description="Start of the time range, inclusive, in UTC for every Dataset: ISO-8601 such as "
        "'2005-07-01' or '2005-07-01T12:00:00', or with an offset, which is converted to UTC (a logged "
        "BGL ts is US Pacific time: '2005-07-01T05:00:00-07:00'); or a relative range such as "
        "'last 7d' or 'last 24h', which ends at the Dataset's last Log Entry, not today. Omit it to "
        "start at the first Log Entry.",
        max_length=64,
    ),
]
UtcTimeToParam = Annotated[
    str | None,
    Field(
        description="End of the time range, exclusive, in UTC like time_from (ISO-8601, an offset is "
        "converted). Omit it to end after the last Log Entry, and always omit it with a relative "
        "time_from.",
        max_length=64,
    ),
]
ComponentsParam = Annotated[
    list[str] | None,
    Field(
        description="Only these Components, for example ['KERNEL'] or ['nova.compute.manager'].",
        max_length=50,
    ),
]
LevelsParam = Annotated[
    list[str] | None, Field(description="Only these levels, for example ['FATAL', 'ERROR'].", max_length=20)
]
SourcesParam = Annotated[
    list[str] | None,
    Field(
        description="Only these sources: BGL node names such as 'R30-M0-N9-C:J16-U01', or OpenStack "
        "instance UUIDs.",
        max_length=100,
    ),
]
AlertCategoriesParam = Annotated[
    list[str] | None,
    Field(
        description="BGL only: only these alert categories, for example ['KERNDTLB', 'KERNMNTF']. "
        "Restricts the Data Scope to BGL.",
        max_length=50,
    ),
]
PatternIdsParam = Annotated[
    list[str] | None,
    Field(
        description="Only these Patterns: 16-hex template_id values, as list_patterns or "
        "count_by(group_by='pattern') return them.",
        max_length=100,
    ),
]
FamilyIdParam = Annotated[
    str | None,
    Field(
        description="Only this Pattern Family (family_id): Patterns whose wording differs only by a "
        "known drift, counted together.",
        max_length=32,
    ),
]


# Composable SQL with its parameters in textual order ----------------------------------------------------


@dataclass(frozen=True)
class Q:
    """A piece of SQL and the parameters of its `%s` placeholders, in the order they appear."""

    sql: sql.Composable
    params: tuple[Any, ...] = ()

    @property
    def text(self) -> str:
        return self.sql.as_string(None)


def q(template: str, *parts: Q | sql.Composable) -> Q:
    """Fill the positional `{}` of `template` in order; parameters follow the parts' order."""
    composables: list[sql.Composable] = []
    params: list[Any] = []
    for part in parts:
        if isinstance(part, Q):
            composables.append(part.sql)
            params.extend(part.params)
        else:
            composables.append(part)
    return Q(sql.SQL(template).format(*composables), tuple(params))


def bound(value: Any, cast: str = "") -> Q:
    """One bound parameter, optionally cast, for example `bound(['a'], '::VARCHAR[]')`."""
    return Q(sql.SQL("%s" + cast), (value,))


def joined(separator: str, parts: Iterable[Q]) -> Q:
    items = list(parts)
    return Q(sql.SQL(separator).join(p.sql for p in items), tuple(v for p in items for v in p.params))


def literal(value: str) -> sql.Composable:
    return sql.Literal(value)


# The validated call --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryFilters:
    """The validated filters every statement applies (the Data Scope without its time range)."""

    query: str | None = None
    components: tuple[str, ...] = ()
    levels: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    alert_categories: tuple[str, ...] = ()
    pattern_ids: tuple[str, ...] = ()
    family_id: str | None = None


@dataclass(frozen=True)
class Part:
    """One Dataset of the Data Scope: where its Log Entries are, how they group and which match."""

    rel: Relation
    window: DatasetWindow
    group_by: str | None
    filters: EntryFilters

    @property
    def dataset(self) -> str:
        return self.rel.dataset.name

    def col(self, concept: str) -> sql.Composable:
        return sql.SQL("e.{}").format(self.rel.column(concept))

    @property
    def clock(self) -> sql.Composable:
        """A Log Entry's time in UTC, which every time comparison and bucket uses (clock.py, #65)."""
        return utc_time(self.rel)

    @property
    def key(self) -> sql.Composable:
        """The group of a Log Entry, never NULL (missing values get their NULL_LABELS label)."""
        if self.group_by is None:
            return literal(ALL)
        if self.group_by == "dataset":
            return literal(self.dataset)
        if self.group_by == "team":
            return sql.SQL("coalesce(o.team, {})").format(literal(NULL_LABELS["team"]))
        return sql.SQL("coalesce({}, {})").format(
            self.col(self.group_by), literal(NULL_LABELS[self.group_by])
        )

    @property
    def source(self) -> Q:
        """FROM: the index relation when there is a full-text query, else the table; plus Ownership."""
        entries = self.rel.index if self.filters.query else self.rel.table
        if self.group_by != "team":
            return q("{} AS e", entries)
        # One Team per (dataset, component): a repeated Ownership row would otherwise count a Log
        # Entry twice. A Component with two Teams goes to the first by name.
        return q(
            "{} AS e LEFT JOIN (SELECT dataset, component, min(team) AS team FROM {} "
            "GROUP BY dataset, component) AS o ON o.dataset = {} AND o.component = {}",
            entries,
            self.rel.ownership_table,
            literal(self.dataset),
            self.col("component"),
        )

    def conditions(self, *, in_window: bool) -> Q:
        """WHERE: the filters, plus the Data Scope's time range inside this Dataset when asked."""
        found: list[Q] = []
        if self.filters.query:
            predicate, params = fts.text_match(self.col("message"), self.filters.query)
            found.append(Q(predicate, tuple(params)))
        for concept, values in (
            ("component", self.filters.components),
            ("level", self.filters.levels),
            ("source", self.filters.sources),
            ("alert_category", self.filters.alert_categories),
            ("pattern", self.filters.pattern_ids),
        ):
            if values:
                found.append(q("{} = ANY({})", self.col(concept), bound(list(values), "::VARCHAR[]")))
        if self.filters.family_id:
            found.append(q("{} = {}", self.col("family_id"), bound(self.filters.family_id)))
        if in_window:
            found.append(self.between(self.window.start, self.window.end))
        return joined(" AND ", found) if found else Q(sql.SQL("TRUE"))

    def between(self, start: datetime, end: datetime) -> Q:
        ts = self.clock
        return q("{} >= {} AND {} < {}", ts, bound(start), ts, bound(end))


@dataclass(frozen=True)
class Prepared:
    """A validated aggregation call: its Data Scope and one Part per Dataset."""

    role: Role
    scope: DataScope
    parts: tuple[Part, ...]
    notes: tuple[str, ...] = ()

    @property
    def group_by(self) -> str | None:
        return self.parts[0].group_by

    def regrouped(self, group_by: str | None) -> tuple[Part, ...]:
        return tuple(replace(part, group_by=group_by) for part in self.parts)


def choose_datasets(
    dataset: str | None, group_by: str | None, alert_categories: Sequence[str] | None
) -> tuple[list[str], list[str]]:
    """The Datasets of the call, narrowed to BGL for BGL-only dimensions; plus a note when narrowed."""
    reason = None
    if group_by in BGL_ONLY_GROUPS:
        reason = f"group_by='{group_by}'"
    elif alert_categories:
        reason = "alert_categories"
    if dataset is None:
        if reason is None:
            return list(DATASET_NAMES), []
        return ["bgl"], [f"{reason} exists only in BGL, so the Data Scope is the BGL Dataset only."]
    if reason is not None and dataset != "bgl":
        raise ScopeError(
            f"{reason} exists only in BGL: OpenStack Log Entries have no node, rack, midplane or alert "
            f"category. Pass dataset='bgl', or group {dataset} by component, level, team or pattern."
        )
    return [dataset], []


def prepare(
    role: Role,
    datasets: Sequence[str],
    *,
    group_by: str | None,
    query: str | None,
    time_from: str | None,
    time_to: str | None,
    components: Sequence[str] | None,
    levels: Sequence[str] | None,
    sources: Sequence[str] | None,
    alert_categories: Sequence[str] | None,
    pattern_ids: Sequence[str] | None,
    family_id: str | None,
    baseline_from: str | None = None,
    baseline_to: str | None = None,
    notes: Sequence[str] = (),
) -> Prepared:
    """Validate the inputs against the Datasets' facts and build the Data Scope and the Parts.

    Every time is UTC (clock.py): the time inputs, the Datasets' ranges and so every window.
    """
    converted: list[str] = []

    def as_utc(name: str, value: str | None) -> str | None:
        found, note = utc_input(name, value)
        converted.extend([note] if note else [])
        return found

    time_from, time_to = as_utc("time_from", time_from), as_utc("time_to", time_to)
    baseline_from, baseline_to = as_utc("baseline_from", baseline_from), as_utc("baseline_to", baseline_to)
    known = {name: dataset_facts(name, role) for name in datasets}
    ranges: dict[str, tuple[datetime, datetime]] = {}
    for name, found in known.items():
        if found.first is not None and found.last is not None:
            ranges[name] = utc_range(name, found.first, found.last)
    empty = [name for name in known if name not in ranges]
    if empty:
        raise ScopeError(
            f"{', '.join(empty)} has no Log Entries in this database, so there is nothing to count. "
            "Call describe_corpus for the Datasets that are loaded."
        )
    components_ = validate_choices("components", components, _values(known, "components"))
    levels_ = validate_choices("levels", levels, _values(known, "levels"))
    alerts_ = validate_choices("alert_categories", alert_categories, _values(known, "alert_categories"))
    sources_ = list(dict.fromkeys(s.strip() for s in sources or () if s.strip())) or None
    text = fts.normalise_query(query)
    corpus_end = {name: last for name, (_, last) in ranges.items()}
    scope = build_scope(
        list(datasets),
        time_from=time_from,
        time_to=time_to,
        corpus_end=corpus_end,
        known_datasets=DATASET_NAMES,
        pattern_ids=pattern_ids,
        family_id=family_id,
        query=text,
        components=components_,
        levels=levels_,
        sources=sources_,
        alert_categories=alerts_,
    )
    scope = scope.model_copy(update={"time_zone": UTC_ZONE, "time_columns": time_columns(scope.datasets)})
    clock = clock_note(
        {name: ranges[name] for name in scope.datasets},
        lead=AGGREGATION_CLOCK_LEAD,
        on_epoch=AGGREGATION_ON_EPOCH,
    )
    notes = [*notes, *converted, clock]
    start, end = time_bounds(scope)
    baseline = _baseline(baseline_from, baseline_to)
    filters = EntryFilters(
        query=text,
        components=tuple(components_ or ()),
        levels=tuple(levels_ or ()),
        sources=tuple(sources_ or ()),
        alert_categories=tuple(alerts_ or ()),
        pattern_ids=tuple(scope.filters.pattern_ids or ()),
        family_id=scope.filters.family_id,
    )
    parts = []
    for name in scope.datasets:
        span = window(name, *ranges[name], start, end, baseline)
        parts.append(Part(relation(name, role, scope), span, group_by, filters))
    return Prepared(role, scope, tuple(parts), tuple(notes))


def _values(known: Mapping[str, DatasetFacts], field_name: str) -> list[str]:
    return [value for found in known.values() for value in getattr(found, field_name)]


def _baseline(baseline_from: str | None, baseline_to: str | None) -> tuple[datetime, datetime] | None:
    if baseline_from is None and baseline_to is None:
        return None
    if baseline_from is None or baseline_to is None:
        raise ScopeError(
            "Give both baseline_from and baseline_to, or neither: without them the baseline is the "
            "period of equal length just before the Data Scope."
        )
    for name, value in (("baseline_from", baseline_from), ("baseline_to", baseline_to)):
        if value.strip().lower().startswith(("last", "past")):
            raise ScopeError(
                f"{name}={value!r} is relative; the baseline period takes ISO-8601 timestamps, for "
                "example baseline_from='2005-07-01', baseline_to='2005-08-01'."
            )
    try:
        span = resolve_time(baseline_from, baseline_to)
    except ScopeError as exc:
        text = str(exc).replace("time_from", "baseline_from").replace("time_to", "baseline_to")
        raise ScopeError(text) from None
    if span.start is None or span.end is None:  # both are given, so both resolve; keeps mypy honest
        raise ScopeError("Give both baseline_from and baseline_to as ISO-8601 timestamps.")
    return span.start, span.end


# Statements ---------------------------------------------------------------------------------------------


def rank_statement(parts: Sequence[Part], top: int, *, show_single_hidden: bool) -> Q:
    """Groups in the Data Scope by count (ties by Dataset and group), the top `top` of them, and one
    roll-up row per Dataset (k and rnk NULL) with its Log Entry total and its number of groups.

    With `show_single_hidden`, a Dataset's only group below the top is returned too, so an `(other)`
    row always stands for at least two groups and a result is truncated exactly when groups are hidden.
    """
    matching = joined(
        "\n    UNION ALL\n    ",
        (
            q(
                "SELECT {} AS dataset, {} AS k FROM {} WHERE {}",
                literal(p.dataset),
                p.key,
                p.source,
                p.conditions(in_window=True),
            )
            for p in parts
        ),
    )
    single = Q(sql.SQL(" OR hidden = 1")) if show_single_hidden else Q(sql.SQL(""))
    return q(
        """WITH matching AS (
    {}
),
grouped AS (SELECT dataset, k, count(*) AS n FROM matching GROUP BY dataset, k),
ranked AS (SELECT dataset, k, n, row_number() OVER (ORDER BY n DESC, dataset, k) AS rnk FROM grouped),
marked AS (
    SELECT dataset, k, n, rnk,
           sum(CASE WHEN rnk > {} THEN 1 ELSE 0 END) OVER (PARTITION BY dataset) AS hidden
    FROM ranked
)
SELECT dataset, k, n, rnk, CAST(NULL AS BIGINT) AS groups FROM marked WHERE rnk <= {}{}
UNION ALL
SELECT dataset, CAST(NULL AS VARCHAR), sum(n), CAST(NULL AS BIGINT), count(*) FROM grouped GROUP BY dataset
ORDER BY rnk NULLS LAST, dataset""",
        matching,
        bound(top),
        bound(top),
        single,
    )


def stats_statement(
    parts: Sequence[Part], keys: Mapping[str, Sequence[str]], shown: Mapping[str, Sequence[str]]
) -> Q:
    """Per key of each Dataset: count, distinct sources, first and last seen in the Data Scope, the
    previous-period count and the median of the zero-filled whole weeks of the Dataset.

    Log Entries whose group is not in `shown[dataset]` count towards the key `(other)`.
    """
    used = [p for p in parts if keys.get(p.dataset)]
    matching = joined(
        "\n    UNION ALL\n    ",
        (
            q(
                "SELECT {} AS dataset, CASE WHEN {} = ANY({}) THEN {} ELSE {} END AS k, {} AS src, {} AS ts, "
                "({}) AS in_scope, {} AS in_prev FROM {} WHERE {}",
                literal(p.dataset),
                p.key,
                bound(list(shown.get(p.dataset, ())), "::VARCHAR[]"),
                p.key,
                literal(OTHER),
                p.col("source"),
                p.clock,
                p.between(p.window.start, p.window.end),
                q("({})", p.between(*p.window.previous)) if p.window.previous else Q(sql.SQL("FALSE")),
                p.source,
                p.conditions(in_window=False),
            )
            for p in used
        ),
    )
    grid = joined(
        "\n    UNION ALL\n    ",
        (
            q(
                # Whole weeks only (after the first Log Entry's week, before the last one's), and only
                # those outside the Data Scope, so a count is never judged against its own weeks.
                "SELECT {} AS dataset, week FROM (SELECT unnest(generate_series("
                "time_bucket(INTERVAL '7 days', {}::TIMESTAMP) + INTERVAL '7 days', "
                "time_bucket(INTERVAL '7 days', {}::TIMESTAMP) - INTERVAL '7 days', "
                "INTERVAL '7 days')) AS week) AS whole "
                "WHERE week + INTERVAL '7 days' <= {}::TIMESTAMP OR week >= {}::TIMESTAMP",
                literal(p.dataset),
                bound(p.window.first),
                bound(p.window.dataset_end - timedelta(seconds=1)),
                bound(p.window.start),
                bound(p.window.end),
            )
            for p in used
        ),
    )
    wanted = joined(
        "\n    UNION ALL\n    ",
        (
            q(
                "SELECT {} AS dataset, unnest({}) AS k",
                literal(p.dataset),
                bound(list(keys[p.dataset]), "::VARCHAR[]"),
            )
            for p in used
        ),
    )
    return q(
        """WITH matching AS (
    {}
),
cur AS (
    SELECT dataset, k, count(*) AS n, count(DISTINCT src) AS sources, min(ts) AS first_seen,
           max(ts) AS last_seen
    FROM matching WHERE in_scope GROUP BY dataset, k
),
weekly AS (
    SELECT dataset, k, time_bucket(INTERVAL '7 days', ts) AS week, count(*) AS n,
           count(*) FILTER (WHERE in_prev) AS prev
    FROM matching GROUP BY dataset, k, week
),
grid AS (
    {}
),
wanted AS (
    {}
),
med AS (
    SELECT wanted.dataset, wanted.k, median(coalesce(weekly.n, 0)) AS median_week, count(*) AS weeks
    FROM wanted JOIN grid ON grid.dataset = wanted.dataset
    LEFT JOIN weekly ON weekly.dataset = wanted.dataset AND weekly.k = wanted.k AND weekly.week = grid.week
    GROUP BY wanted.dataset, wanted.k
),
prev AS (SELECT dataset, k, sum(prev) AS prev_period FROM weekly GROUP BY dataset, k)
SELECT wanted.dataset, wanted.k, coalesce(cur.n, 0) AS n, coalesce(cur.sources, 0) AS sources,
       cur.first_seen, cur.last_seen, coalesce(prev.prev_period, 0) AS prev_period, med.median_week,
       coalesce(med.weeks, 0) AS weeks
FROM wanted
LEFT JOIN cur ON cur.dataset = wanted.dataset AND cur.k = wanted.k
LEFT JOIN prev ON prev.dataset = wanted.dataset AND prev.k = wanted.k
LEFT JOIN med ON med.dataset = wanted.dataset AND med.k = wanted.k
ORDER BY wanted.dataset, wanted.k""",
        matching,
        grid,
        wanted,
    )


# Running and reading the statements ---------------------------------------------------------------------


@dataclass(frozen=True)
class Ranked:
    """What `rank_statement` returned."""

    groups: list[tuple[str, str, int]]
    """(dataset, group, count) of the returned groups, in rank order."""
    totals: dict[str, int]
    """Log Entries per Dataset in the Data Scope."""
    group_counts: dict[str, int]
    """Groups per Dataset in the Data Scope."""

    @property
    def total(self) -> int:
        return sum(self.totals.values())

    @property
    def groups_total(self) -> int:
        return sum(self.group_counts.values())

    def shown(self, dataset: str) -> list[str]:
        return [group for name, group, _ in self.groups if name == dataset]

    def hidden(self, dataset: str) -> int:
        return self.group_counts.get(dataset, 0) - len(self.shown(dataset))


def run(statement: Q, role: Role) -> QueryResult:
    return fetch_all(statement.text, statement.params, role=role)


def read_ranked(result: QueryResult) -> Ranked:
    groups: list[tuple[str, str, int]] = []
    totals: dict[str, int] = {}
    counts: dict[str, int] = {}
    for dataset, group, n, rnk, group_count in result.rows:
        if rnk is None:
            totals[str(dataset)] = int(n)
            counts[str(dataset)] = int(group_count)
        else:
            groups.append((str(dataset), str(group), int(n)))
    return Ranked(groups, totals, counts)


@dataclass(frozen=True)
class GroupStats:
    """One row of `stats_statement`."""

    count: int
    sources: int
    first_seen: datetime | None
    last_seen: datetime | None
    prev_period: int
    median_week: float | None
    weeks: int


def read_stats(result: QueryResult) -> dict[tuple[str, str], GroupStats]:
    return {
        (str(dataset), str(group)): GroupStats(
            int(n),
            int(sources),
            first,
            last,
            int(prev),
            None if median is None else float(median),
            int(weeks),
        )
        for dataset, group, n, sources, first, last, prev, median, weeks in result.rows
    }


@dataclass(frozen=True)
class Baseline:
    """The baseline columns of one count."""

    prev_period: int | None
    median_week: int | float | None
    expected: float | None
    basis: str
    """named_period, median_week, previous_period, or none."""
    flag: str


def baseline_of(count: int, stats: GroupStats, span: DatasetWindow) -> Baseline:
    previous = stats.prev_period if span.previous is not None else None
    median = number(stats.median_week) if stats.weeks >= MIN_WEEKS else None
    verdict = judge(
        count,
        median_week=median,
        weeks=stats.weeks,
        days=span.days,
        previous=previous,
        previous_days=span.previous_days,
        previous_named=span.previous_named,
    )
    return Baseline(previous, median, verdict.expected, verdict.basis or "none", verdict.flag)


def number(value: float | None) -> int | float | None:
    """A float as an int when it is integral, so medians read as counts."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def share(count: int, total: int) -> float:
    """`count` as a percentage of `total`: two decimals from 1 %, four significant digits below it,
    so a small group never reads as 0.0."""
    if not total or not count:
        return 0.0
    pct = 100 * count / total
    return round(pct, 2) if pct >= 1 else float(f"{pct:.4g}")


def whole_weeks(stats: Mapping[tuple[str, str], GroupStats], parts: Sequence[Part]) -> dict[str, int]:
    found = {p.dataset: 0 for p in parts}
    for (dataset, _), row in stats.items():
        found[dataset] = max(found.get(dataset, 0), row.weeks)
    return found


def baseline_summary(parts: Sequence[Part], weeks: Mapping[str, int]) -> dict[str, Any]:
    """The windows behind the baselines, per Dataset, for `summary['baseline']`."""
    out: dict[str, Any] = {}
    for p in parts:
        span = p.window
        out[p.dataset] = {
            "from": iso(span.start),
            "to": iso(span.end),
            "days": round(span.days, 2),
            "previous_from": iso(span.previous[0]) if span.previous else None,
            "previous_to": iso(span.previous[1]) if span.previous else None,
            "whole_weeks_outside_scope": weeks.get(p.dataset, 0),
        }
    return out


def common_notes(prepared: Prepared, weeks: Mapping[str, int]) -> list[str]:
    """Notes every aggregation result carries: Log Entries not Incidents, and the baseline method."""
    windows = [p.window for p in prepared.parts]
    notes = [LOG_ENTRIES_NOTE, method_note(windows, weeks), *few_weeks_notes(windows, weeks), *prepared.notes]
    if prepared.group_by == "team":
        notes.append(OWNERSHIP_NOTE)
    return notes


def unassigned_pattern_notes(ranked: Ranked, group_by: str | None) -> list[str]:
    """When grouping by Pattern finds no Pattern ids at all in a Dataset, say why and what to use."""
    if group_by != "pattern":
        return []
    notes = []
    for dataset in ranked.totals:
        if ranked.shown(dataset) == [NULL_LABELS["pattern"]] and ranked.hidden(dataset) == 0:
            hint = (
                " For BGL, group_by='alert_category' groups the alert lines by category."
                if dataset == "bgl"
                else ""
            )
            notes.append(
                f"No Pattern ids are assigned in {dataset} yet (the Pattern dictionary has not been "
                f"built), so all its Log Entries are in '(no Pattern)'.{hint}"
            )
    return notes


@dataclass
class Collected:
    """The statements one tool call ran, in order."""

    results: list[QueryResult] = field(default_factory=list)

    def add(self, result: QueryResult) -> QueryResult:
        self.results.append(result)
        return result

    def combined(self, *, total: int, rows: Sequence[Sequence[Any]]) -> QueryResult:
        return QueryResult.combine(*self.results, total=total, rows=rows)


__all__ = [
    "AGGREGATION_CLOCK_LEAD",
    "AGGREGATION_ON_EPOCH",
    "ALL",
    "BGL_ONLY_GROUPS",
    "GROUP_BYS",
    "LOG_ENTRIES_NOTE",
    "NO_MATCH_NOTE",
    "NULL_LABELS",
    "OTHER",
    "SOURCES_NOTE",
    "AlertCategoriesParam",
    "Baseline",
    "Collected",
    "ComponentsParam",
    "DatasetName",
    "EntryFilters",
    "FamilyIdParam",
    "GroupBy",
    "GroupStats",
    "LevelsParam",
    "Part",
    "PatternIdsParam",
    "Prepared",
    "Q",
    "QueryParam",
    "Ranked",
    "SourcesParam",
    "UtcTimeFromParam",
    "UtcTimeToParam",
    "baseline_of",
    "baseline_summary",
    "bound",
    "choose_datasets",
    "common_notes",
    "iso",
    "joined",
    "number",
    "prepare",
    "q",
    "rank_statement",
    "read_ranked",
    "read_stats",
    "run",
    "share",
    "stats_statement",
    "unassigned_pattern_notes",
    "whole_weeks",
]
