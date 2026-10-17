"""`count_by`: exact Log Entry counts grouped by one dimension, with a baseline next to every count."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from serenedb_log_mcp.baseline import DatasetWindow
from serenedb_log_mcp.evidence import RoleParam
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import (
    LOG_ENTRIES_NOTE,
    NO_MATCH_NOTE,
    OTHER,
    SOURCES_NOTE,
    AlertCategoriesParam,
    Collected,
    ComponentsParam,
    DatasetName,
    FamilyIdParam,
    GroupBy,
    GroupStats,
    LevelsParam,
    PatternIdsParam,
    Prepared,
    QueryParam,
    SourcesParam,
    TimeFromParam,
    TimeToParam,
    baseline_of,
    baseline_summary,
    choose_datasets,
    common_notes,
    iso,
    prepare,
    rank_statement,
    read_ranked,
    read_stats,
    run,
    share,
    stats_statement,
    unassigned_pattern_notes,
    whole_weeks,
)

DEFAULT_TOP = 25
MAX_TOP = 100
BASELINE_COLUMNS = ("prev_period", "median_week", "expected", "baseline_flag")


def register(server: MCPServer) -> None:
    """Register `count_by` on `server`."""

    @evidence_tool(server, title="Count Log Entries by one dimension, with baselines")
    def count_by(
        role: RoleParam,
        group_by: Annotated[
            GroupBy,
            Field(
                description="The dimension to group by: dataset, component, team (via the Ownership "
                "Table), level, node, rack, midplane, alert_category (these four BGL only) or pattern "
                "(template_id)."
            ),
        ],
        dataset: Annotated[
            DatasetName | None,
            Field(description="'bgl' or 'openstack'; omit it for the whole Base Corpus (both Datasets)."),
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
                le=MAX_TOP,
                description=f"How many groups to list (default {DEFAULT_TOP}, at most {MAX_TOP}); the "
                "rest are summed in an (other) row per Dataset.",
            ),
        ] = DEFAULT_TOP,
        baseline_from: Annotated[
            str | None,
            Field(
                description="Optional baseline period start (ISO-8601), with baseline_to; it replaces "
                "the default baseline, the period of equal length just before the Data Scope."
            ),
        ] = None,
        baseline_to: Annotated[
            str | None, Field(description="Optional baseline period end, exclusive (ISO-8601).")
        ] = None,
    ) -> ToolOutcome:
        """Exact counts of matching Log Entries grouped by one dimension, with a baseline per count.

        Purpose: answer "how often", "which Components, Teams, nodes or categories" and "is this
        unusual" over any Data Scope, including the whole Base Corpus. Counts are Log Entries, not
        Incidents: one failing node can write thousands of lines. Quote exact numbers, never round,
        and quote share_pct, top_n_sum and other_count instead of adding or dividing yourself. Use
        search_logs for example Log Entries and trend for the change over time.
        Inputs: role, group_by, and optional dataset, query (all words; "quoted phrase"), time_from,
        time_to, components, levels, sources, alert_categories, pattern_ids, family_id, top,
        baseline_from and baseline_to.
        Outputs: the Evidence envelope. rows: one per group, largest first, with columns dataset,
        the group_by value, count, share_pct, distinct_sources, first_seen, last_seen, prev_period,
        median_week, expected and baseline_flag (normal, unusual_high, unusual_low or no_baseline),
        plus an (other) row per Dataset for the groups beyond top. summary: total, groups_total,
        top_n_sum, top_n_share_pct, other_count, other_groups and the baseline periods. notes:
        the baseline method. total_matches counts groups, not Log Entries.
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
            baseline_from=baseline_from,
            baseline_to=baseline_to,
            notes=notes,
        )
        return _count(prepared, top)


def _count(prepared: Prepared, top: int) -> ToolOutcome:
    group_by = prepared.group_by
    assert group_by is not None
    collected = Collected()
    ranked = read_ranked(
        collected.add(run(rank_statement(prepared.parts, top, show_single_hidden=True), prepared.role))
    )
    columns = ["dataset"] if group_by == "dataset" else ["dataset", group_by]
    columns += ["count", "share_pct", "distinct_sources", "first_seen", "last_seen", *BASELINE_COLUMNS]
    if ranked.total == 0:
        return ToolOutcome(
            result=collected.combined(total=0, rows=[]),
            scope=prepared.scope,
            columns=columns,
            summary=_summary(group_by, 0, 0, 0, 0, 0),
            notes=[LOG_ENTRIES_NOTE, *prepared.notes, NO_MATCH_NOTE],  # no count, so no baseline
            rows=[],
            total_matches=0,
        )
    with_other = [p.dataset for p in prepared.parts if ranked.hidden(p.dataset) >= 2]
    shown = {p.dataset: ranked.shown(p.dataset) for p in prepared.parts}
    keys = {name: [*groups, *([OTHER] if name in with_other else [])] for name, groups in shown.items()}
    stats = read_stats(collected.add(run(stats_statement(prepared.parts, keys, shown), prepared.role)))
    windows = {p.dataset: p.window for p in prepared.parts}

    rows: list[list[Any]] = []
    for name, group, n in ranked.groups:
        row = stats[(name, group)]
        if row.count != n:
            raise RuntimeError(
                f"the two count_by statements disagree on {name} {group!r} ({n} against {row.count}); "
                "the tables changed between them"
            )
        rows.append(_row(group_by, name, group, n, ranked.total, row, windows[name]))
    named_sum = sum(n for _, _, n in ranked.groups)
    for name in with_other:
        row = stats[(name, OTHER)]
        expected_other = ranked.totals[name] - sum(n for d, _, n in ranked.groups if d == name)
        if row.count != expected_other:
            raise RuntimeError(
                f"the (other) row of {name} has {row.count} Log Entries, the ranking {expected_other}"
            )
        rows.append(_row(group_by, name, OTHER, row.count, ranked.total, row, windows[name]))

    weeks = whole_weeks(stats, prepared.parts)
    other_groups = ranked.groups_total - len(ranked.groups)
    other_count = ranked.total - named_sum
    summary = _summary(
        group_by, ranked.total, ranked.groups_total, len(ranked.groups), named_sum, other_groups
    )
    summary["baseline"] = baseline_summary(prepared.parts, weeks)
    notes = [*common_notes(prepared, weeks), SOURCES_NOTE, *unassigned_pattern_notes(ranked, group_by)]
    truncation = (
        f"Showing {len(ranked.groups)} of {ranked.groups_total:,} groups; the (other) "
        f"{'row holds' if len(with_other) == 1 else 'rows hold'} the remaining {other_groups:,} "
        f"groups with {other_count:,} Log Entries, so the rows add up to the total exactly."
    )
    return ToolOutcome(
        result=collected.combined(total=ranked.groups_total, rows=rows),
        scope=prepared.scope,
        columns=columns,
        summary=summary,
        notes=notes,
        rows=rows,
        total_matches=ranked.groups_total,
        truncation_note=truncation,
    )


def _row(
    group_by: str, dataset: str, group: str, n: int, total: int, stats: GroupStats, span: DatasetWindow
) -> list[Any]:
    """One output row; group_by='dataset' has no separate group column (a Dataset is one group)."""
    base = baseline_of(n, stats, span)
    label = [] if group_by == "dataset" else [group]
    return [
        dataset,
        *label,
        n,
        share(n, total),
        stats.sources,
        iso(stats.first_seen),
        iso(stats.last_seen),
        base.prev_period,
        base.median_week,
        base.expected,
        base.flag,
    ]


def _summary(
    group_by: str, total: int, groups_total: int, named: int, named_sum: int, other_groups: int
) -> dict[str, Any]:
    return {
        "group_by": group_by,
        "total": total,
        "groups_total": groups_total,
        "top_n": named,
        "top_n_sum": named_sum,
        "top_n_share_pct": share(named_sum, total),
        "other_count": total - named_sum,
        "other_groups": other_groups,
    }
