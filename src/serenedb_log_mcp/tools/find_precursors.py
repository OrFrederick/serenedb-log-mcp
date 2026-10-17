"""`find_precursors`: Patterns that precede a target Pattern more often than chance (T16, #1, #4).

Precursor mining with a guard (docs/decisions.md, round 2): across every Incident of the target
Pattern, find which Patterns appear within `window_s` seconds before it, compare that rate with a
baseline, and report a candidate only when it passes a stated minimum support and lift. Otherwise
the answer is "no consistent precursor found". Every row is a Hypothesis (Glossary: Cascade).

One statement does the work in SereneDB:

1. the target Incidents: the target Pattern's Log Entries in the Data Scope, chained across sources
   at the default gap (300 s, gaps on epoch_utc), as list_incidents chains them;
2. the units: one per target Incident (its first Log Entry) or, with `same_source`, one per target
   Incident and source (the first target Log Entry on that source);
3. the hits: every Log Entry of another Pattern 1 s to `window_s` s before a unit (on the unit's
   source with `same_source`). The minimum lag of 1 s is #1's Cascade rule, so Log Entries written
   in the same second as the target, which are artefacts of one event, never count;
4. per candidate Pattern: support (target Incidents with a hit), units preceded, the rate, and the
   delay from the closest preceding Log Entry to the unit (median and range);
5. the baseline: a deterministic probe grid, one probe every `PROBE_STEP_S` seconds on the UTC
   clock across the Data Scope (per source of the units with `same_source`). A probe is hit when a
   Log Entry of the candidate falls in the same window before it. The baseline rate is the share of
   probes hit, and lift = rate / baseline rate. A baseline of zero hits counts as one hit, so the lift
   is then a lower bound rather than infinite.

Because `window_s` is at most `PROBE_STEP_S`, a Log Entry falls into the window of at most one probe,
so the probe hits are counted with arithmetic on each Log Entry instead of a join against the grid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp.corpus.datasets import DEFAULT_GAP_S, LIFECYCLE_KEY, DatasetName
from serenedb_log_mcp.corpus.facts import dataset_facts
from serenedb_log_mcp.db import QueryResult
from serenedb_log_mcp.evidence import RoleParam, ScopeFilters
from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import (
    Part,
    Q,
    SourcesParam,
    TimeFromParam,
    TimeToParam,
    bound,
    choose_datasets,
    prepare,
    q,
    run,
)
from serenedb_log_mcp.tools.list_incidents import pattern_key_of, source_of

__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MIN_LIFT",
    "DEFAULT_MIN_SUPPORT",
    "DEFAULT_WINDOW_S",
    "MAX_LIMIT",
    "MAX_WINDOW_S",
    "MIN_LAG_S",
    "NO_PRECURSOR",
    "PROBE_STEP_S",
    "register",
    "target_key",
]

DEFAULT_WINDOW_S = 60
MIN_LAG_S = 1
PROBE_STEP_S = 3600
MAX_WINDOW_S = PROBE_STEP_S
DEFAULT_MIN_SUPPORT = 3
DEFAULT_MIN_LIFT = 2.0
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
NO_PRECURSOR = "No consistent precursor found"

COLUMNS = [
    "precursor",
    "support",
    "units_preceded",
    "rate",
    "probes_hit",
    "baseline_rate",
    "lift",
    "delay_median_s",
    "delay_min_s",
    "delay_max_s",
    "precursor_entries",
]
_TOTALS = (
    "incidents_total",
    "units_total",
    "sources_total",
    "probes",
    "candidates_total",
    "supported_total",
    "passed_total",
)


def target_key(dataset: str, value: str, alert_categories: Sequence[str]) -> str:
    """The canonical pattern_key of the target: a BGL alert category or a template id."""
    key = pattern_key_of(dataset, value, alert_categories)
    if key is None:
        raise ScopeError(
            "target_pattern is empty. Pass a BGL alert category such as 'APPSEV' or a 16-hex "
            "template_id from list_patterns."
        )
    if key == LIFECYCLE_KEY:
        raise ScopeError(
            f"{LIFECYCLE_KEY!r} is a kind of Request Failure, not a Pattern of Log Entries, so nothing "
            "can precede it in time. Pass the template_id of a failing Pattern from list_patterns."
        )
    return key


@dataclass(frozen=True)
class _Call:
    part: Part
    target: str
    window_s: int
    same_source: bool
    min_support: int
    min_lift: float
    limit: int


def _statement(call: _Call) -> Q:
    """The passing candidates, each row carrying the totals; one row of totals when none passes."""
    part, rel = call.part, call.part.rel
    pattern = part.col("pattern_key")
    epoch = part.col("epoch")
    unit_src = sql.SQL("src" if call.same_source else "''")
    entry_src = source_of(rel) if call.same_source else sql.SQL("''")
    same_source = q(" AND {} = u.src", source_of(rel)) if call.same_source else Q(sql.SQL(""))
    covered_source = (
        q(" AND {} IN (SELECT src FROM units)", source_of(rel)) if call.same_source else Q(sql.SQL(""))
    )
    probes_per_slot = sql.SQL("t.sources_total" if call.same_source else "1")
    window, step, lag = bound(call.window_s), bound(PROBE_STEP_S), bound(MIN_LAG_S)
    return q(
        """WITH target AS (
    SELECT {} AS entry_id, {} AS epoch_utc, {} AS src FROM {} WHERE {} = {} AND {}
),
gapped AS (SELECT *, lag(epoch_utc) OVER (ORDER BY epoch_utc, entry_id) AS previous FROM target),
chained AS (
    SELECT *, CAST(sum(CASE WHEN previous IS NULL OR epoch_utc - previous > {} THEN 1 ELSE 0 END)
                   OVER (ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING) AS BIGINT) AS incident
    FROM gapped
),
units AS (SELECT incident, {} AS src, min(epoch_utc) AS t FROM chained GROUP BY incident, {}),
hits AS (
    SELECT u.incident, u.src, {} AS k, u.t - max({}) AS delay, count(*) AS n
    FROM units AS u JOIN {} AS e ON {} >= u.t - {} AND {} <= u.t - {}{}
    WHERE {} IS NOT NULL AND {} <> {}
    GROUP BY u.incident, u.src, u.t, {}
),
candidates AS (
    SELECT k, count(DISTINCT incident) AS support, count(*) AS units_preceded,
           CAST(sum(n) AS BIGINT) AS entries,
           quantile_cont(delay, 0.5) AS delay_median, min(delay) AS delay_min, max(delay) AS delay_max
    FROM hits GROUP BY k
),
kept AS (SELECT * FROM candidates WHERE support >= {}),
bounds AS (SELECT min({}) AS lo, max({}) AS hi FROM {} AS e WHERE {}),
grid AS (
    SELECT g0, greatest(coalesce(CAST(floor((hi - g0) / {}) AS BIGINT) + 1, 0), 0) AS probes
    FROM (SELECT ceil((lo + {}) / {}) * {} AS g0, hi FROM bounds) AS b
),
covered AS (
    SELECT DISTINCT {} AS k, {} AS src, floor(({} + {} - g.g0) / {}) AS slot
    FROM {} AS e CROSS JOIN grid AS g
    WHERE {} IN (SELECT k FROM kept){}
      AND {} >= g.g0 - {} AND {} <= g.g0 + (g.probes - 1) * {} - {}
      AND g.g0 + floor(({} + {} - g.g0) / {}) * {} >= {} + {}
),
probe_hits AS (SELECT k, count(*) AS probes_hit FROM covered GROUP BY k),
totals AS (
    SELECT (SELECT count(DISTINCT incident) FROM units) AS incidents_total,
           (SELECT count(*) FROM units) AS units_total,
           (SELECT count(DISTINCT src) FROM chained) AS sources_total,
           (SELECT probes FROM grid) AS probes,
           (SELECT count(*) FROM candidates) AS candidates_total,
           (SELECT count(*) FROM kept) AS supported_total
),
scored AS (
    SELECT kept.*, coalesce(p.probes_hit, 0) AS probes_hit,
           CAST(kept.units_preceded AS DOUBLE) / t.units_total AS rate,
           CAST(coalesce(p.probes_hit, 0) AS DOUBLE) / nullif(t.probes * {}, 0) AS baseline_rate,
           (CAST(kept.units_preceded AS DOUBLE) / t.units_total)
               / (CAST(greatest(coalesce(p.probes_hit, 0), 1) AS DOUBLE) / nullif(t.probes * {}, 0)) AS lift
    FROM kept CROSS JOIN totals AS t LEFT JOIN probe_hits AS p ON p.k = kept.k
),
passed AS (SELECT * FROM scored WHERE lift >= {})
SELECT t.*, (SELECT count(*) FROM passed) AS passed_total,
       s.k, s.support, s.units_preceded, round(s.rate, 4), s.probes_hit, round(s.baseline_rate, 6),
       round(s.lift, 2), round(s.delay_median, 3), round(s.delay_min, 3), round(s.delay_max, 3), s.entries
FROM totals AS t
LEFT JOIN (SELECT * FROM passed ORDER BY support DESC, lift DESC, k LIMIT {}) AS s ON TRUE
ORDER BY s.support DESC, s.lift DESC, s.k""",
        part.col("entry_id"),
        epoch,
        source_of(rel),
        part.source,
        pattern,
        bound(call.target),
        part.conditions(in_window=True),
        bound(DEFAULT_GAP_S),
        unit_src,
        unit_src,
        pattern,
        epoch,
        rel.table,
        epoch,
        window,
        epoch,
        lag,
        same_source,
        pattern,
        pattern,
        bound(call.target),
        pattern,
        bound(call.min_support),
        epoch,
        epoch,
        rel.table,
        part.between(part.window.start, part.window.end),
        step,
        window,
        step,
        step,
        pattern,
        entry_src,
        epoch,
        window,
        step,
        rel.table,
        pattern,
        covered_source,
        epoch,
        window,
        epoch,
        step,
        lag,
        epoch,
        window,
        step,
        step,
        epoch,
        lag,
        probes_per_slot,
        probes_per_slot,
        bound(call.min_lift),
        bound(call.limit),
    )


def _number(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _read(rows: Sequence[Sequence[Any]]) -> tuple[dict[str, int], list[list[Any]]]:
    totals = {name: int(rows[0][i] or 0) for i, name in enumerate(_TOTALS)} if rows else {}
    listed = [
        [_number(value) for value in row[len(_TOTALS) :]] for row in rows if row[len(_TOTALS)] is not None
    ]
    return totals, listed


def _notes(call: _Call, summary: dict[str, Any]) -> list[str]:
    unit = (
        "a target Incident on one source (the first target Log Entry on that source)"
        if call.same_source
        else "a target Incident (its first Log Entry)"
    )
    where = (
        "on the same source (BGL node; OpenStack instance, or host)" if call.same_source else "on any source"
    )
    notes = [
        "Every row is a Hypothesis, not an Observed Fact about cause: the Pattern appeared before the "
        "target more often than the baseline, and appearing earlier in time does not establish a "
        "cause. Say so in the answer, under Hypotheses.",
        f"Method: the target Incidents are the Log Entries of {call.target!r} in the Data Scope chained "
        f"across sources at a gap of {DEFAULT_GAP_S} s. A unit is {unit}. A Log Entry of another Pattern "
        f"{where} {MIN_LAG_S} s to {call.window_s} s before a unit precedes it; the minimum lag of "
        f"{MIN_LAG_S} s means same-timestamp artefacts (Log Entries written in the same second as the "
        "target) never count. support counts target Incidents preceded; rate is units_preceded / "
        "units_total.",
        f"Thresholds: min_support={call.min_support} target Incidents and min_lift={call.min_lift:g}. "
        f"{summary['candidates_total']} Patterns appeared in a window, {summary['supported_total']} "
        f"reached min_support and {summary['passed_total']} passed both; only those are listed.",
        f"Baseline: a deterministic probe grid, one probe every {PROBE_STEP_S} s on the UTC clock across "
        "the Data Scope"
        + (", for each source of the units" if call.same_source else "")
        + f" ({summary['probes_total']:,} probes). A probe is hit when the candidate Pattern has a Log "
        f"Entry {MIN_LAG_S} s to {call.window_s} s before it; baseline_rate is probes_hit / probes_total "
        "and lift is rate / baseline_rate. A candidate that hits no probe counts as hitting one, so its "
        "lift is a lower bound.",
        "Delays: delay_median_s, delay_min_s and delay_max_s are the median and range, over the units "
        + ("(one per source)" if call.same_source else "(one per target Incident)")
        + ", of the time from the closest preceding Log Entry of the candidate to the unit. Quote them "
        "per source, with the median and the range.",
    ]
    if summary["passed_total"] == 0:
        if summary["incidents_total"] == 0:
            reason = f"the target Pattern {call.target!r} has no Log Entries in this Data Scope"
        else:
            reason = (
                f"no Pattern passed min_support={call.min_support} and min_lift={call.min_lift:g} "
                f"within {call.window_s} s before {summary['incidents_total']:,} target Incidents"
            )
        notes.append(
            f"{NO_PRECURSOR}: {reason}. Report it as such; do not name a precursor. A wider window_s, "
            "same_source=false or lower thresholds can be tried, and say which ones were used."
        )
    if summary["probes_total"] == 0:
        notes.append(
            f"The Data Scope is too short for a probe every {PROBE_STEP_S} s, so there is no baseline and "
            "no lift. Widen the time range."
        )
    return notes


def register(server: MCPServer) -> None:
    """Register `find_precursors` on `server`."""

    @evidence_tool(server, title="Patterns that precede a target Pattern, with support and lift")
    def find_precursors(
        role: RoleParam,
        dataset: Annotated[DatasetName, Field(description="'bgl' or 'openstack': one Dataset per call.")],
        target_pattern: Annotated[
            str,
            Field(
                description="The Pattern whose precursors to find: a BGL alert category such as 'APPSEV' "
                "(the alert_category of count_by and search_logs) or a 16-hex template_id from "
                "list_patterns (either Dataset).",
                min_length=1,
                max_length=64,
            ),
        ],
        window_s: Annotated[
            int,
            Field(
                ge=MIN_LAG_S + 1,
                le=MAX_WINDOW_S,
                description=f"Seconds before each target Incident in which a precursor must appear "
                f"(default {DEFAULT_WINDOW_S}, at most {MAX_WINDOW_S}). The minimum lag is fixed at "
                f"{MIN_LAG_S} s.",
            ),
        ] = DEFAULT_WINDOW_S,
        same_source: Annotated[
            bool,
            Field(
                description="True (default): the precursor must be on the same source as the target (BGL "
                "node, OpenStack instance or host), as a Cascade is defined. False: on any source."
            ),
        ] = True,
        min_support: Annotated[
            int,
            Field(
                ge=1,
                description=f"Minimum number of target Incidents a precursor must precede (default "
                f"{DEFAULT_MIN_SUPPORT}).",
            ),
        ] = DEFAULT_MIN_SUPPORT,
        min_lift: Annotated[
            float,
            Field(
                ge=1.0,
                le=1_000_000.0,
                description=f"Minimum lift, rate over the probe baseline (default {DEFAULT_MIN_LIFT:g}).",
            ),
        ] = DEFAULT_MIN_LIFT,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_LIMIT,
                description=f"How many passing precursors to list (default {DEFAULT_LIMIT}, at most "
                f"{MAX_LIMIT}).",
            ),
        ] = DEFAULT_LIMIT,
        time_from: TimeFromParam = None,
        time_to: TimeToParam = None,
        sources: SourcesParam = None,
    ) -> ToolOutcome:
        """Patterns that precede a target Pattern more often than chance, each a Hypothesis.

        Purpose: answer "what usually happens before X?" with measured support and lift, never a
        guess. The target Pattern's Log Entries in the Data Scope are chained into Incidents
        (gap 300 s). A Pattern precedes an Incident when one of its Log Entries falls between
        1 s (the fixed minimum lag, so same-timestamp artefacts never count) and window_s
        seconds (default 60) before it, on the same source by default. Each candidate is
        compared with a baseline from a deterministic hourly probe grid, and only candidates
        that pass both min_support (target Incidents preceded, default 3) and min_lift (default
        2) are returned. When none passes, notes say "no consistent precursor found": report that,
        and never name a precursor then. Every row is a Hypothesis, not a cause; quote support,
        rate, lift and the delays (median and range per source) with the thresholds.
        Inputs: role, dataset, target_pattern (an alert category or a template_id from
        list_patterns), and optional window_s (default 60, at most 3600), same_source (default
        true), min_support, min_lift, limit (default 20, at most 50), time_from, time_to and
        sources (only target Log Entries on these sources).
        Outputs: the Evidence envelope. rows: one per passing precursor with precursor (its
        pattern_key), support, units_preceded, rate, probes_hit, baseline_rate, lift,
        delay_median_s, delay_min_s, delay_max_s and precursor_entries. summary: incidents_total
        (target Incidents), units_total, sources_total, probes_total, candidates_total,
        supported_total, passed_total, window_s, min_lag_s, same_source and the thresholds.
        notes: the Hypothesis label, the method, the thresholds and the baseline method.
        total_matches counts passing precursors.
        """
        datasets, _ = choose_datasets(dataset, None, None)
        facts = dataset_facts(dataset, role)
        key = target_key(dataset, target_pattern, list(facts.alert_categories))
        prepared = prepare(
            role,
            datasets,
            group_by=None,
            query=None,
            time_from=time_from,
            time_to=time_to,
            components=None,
            levels=None,
            sources=sources,
            alert_categories=None,
            pattern_ids=None,
            family_id=None,
        )
        scope = prepared.scope.model_copy(
            update={
                "filters": ScopeFilters.of(
                    **prepared.scope.filters.model_dump(exclude_none=True),
                    target_pattern=key,
                    window_s=window_s,
                    min_lag_s=MIN_LAG_S,
                    same_source=same_source,
                    min_support=min_support,
                    min_lift=min_lift,
                )
            }
        )
        call = _Call(prepared.parts[0], key, window_s, same_source, min_support, min_lift, limit)
        result = run(_statement(call), role)
        totals, rows = _read(result.rows)
        probes = totals.get("probes", 0) * (totals.get("sources_total", 0) if same_source else 1)
        summary: dict[str, Any] = {
            "dataset": dataset,
            "target_pattern": key,
            "window_s": window_s,
            "min_lag_s": MIN_LAG_S,
            "same_source": same_source,
            "min_support": min_support,
            "min_lift": min_lift,
            "gap_s": DEFAULT_GAP_S,
            "incidents_total": totals.get("incidents_total", 0),
            "units_total": totals.get("units_total", 0),
            "sources_total": totals.get("sources_total", 0),
            "probe_step_s": PROBE_STEP_S,
            "probes_total": probes,
            "candidates_total": totals.get("candidates_total", 0),
            "supported_total": totals.get("supported_total", 0),
            "passed_total": totals.get("passed_total", 0),
        }
        total = summary["passed_total"]
        return ToolOutcome(
            result=QueryResult.combine(result, total=total, rows=rows),
            scope=scope,
            columns=COLUMNS,
            summary=summary,
            notes=_notes(call, summary),
            rows=rows,
            total_matches=total,
            truncation_note=(
                f"Showing {len(rows)} of {total:,} passing precursors, by support then lift; "
                "passed_total in summary counts all of them."
            ),
        )
