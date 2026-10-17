"""`list_incidents`: Incidents with Spread and Shape, side by side with the Log Entry counts (T27, #1).

An Incident is a maximal chain of Log Entries of one Pattern where each entry follows the previous
one within `gap_s` (Glossary). Two ways to answer a call, decided by `reads_table`:

- the precomputed table (`sql/derive/30_incidents.sql` for BGL, `35_os_incidents.sql` for the
  OpenStack Request Failures), for a call with the default parameters (gap_s = 300, merge_scope =
  all) and no filter other than `pattern_key`, which only selects rows of the table;
- the same chaining at query time, over the filtered Log Entries only, for every other call. A call
  with other parameters must narrow the Data Scope, because chaining every Log Entry of a Dataset
  with Spread takes tens of seconds (#1).

Both ways run one statement that returns the listed Incidents together with the totals over all of
them (Incidents, Log Entries, Shapes) and the Spread totals over their Log Entries, so the summary
always shows Log Entries, Incidents and Spread together.

OpenStack Incidents are Request Failure Incidents: only Log Entries at ERROR or CRITICAL, or with an
HTTP status of 500 or more, are chained, and the precomputed table adds one Incident per instance
whose lifecycle never completes (`LIFECYCLE_KEY`). Their Spread is sources: the instance a Log
Entry names, or its host (the nova service whose log carries it) when it names none.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from psycopg import sql
from pydantic import Field

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus.datasets import (
    DEFAULT_GAP_S,
    DEFAULT_MERGE_SCOPE,
    LIFECYCLE_KEY,
    SHAPES,
    DatasetName,
    Relation,
)
from serenedb_log_mcp.corpus.facts import dataset_facts
from serenedb_log_mcp.db import QueryResult
from serenedb_log_mcp.evidence import DataScope, RoleParam, ScopeFilters
from serenedb_log_mcp.scope import ScopeError, validate_choice
from serenedb_log_mcp.toolkit import ToolOutcome, evidence_tool
from serenedb_log_mcp.tools._aggregate import (
    AlertCategoriesParam,
    ComponentsParam,
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
    literal,
    prepare,
    q,
    run,
)

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "SORTS",
    "check_similar_to",
    "pattern_key_of",
    "reads_table",
    "register",
    "source_of",
]

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_GAP_S = 86_400
Sort = Literal["entries", "spread", "time", "recent", "duration"]
MergeScope = Literal["all", "source"]
SORTS: dict[str, str] = {
    "entries": "entries DESC",
    "spread": "coalesce(midplanes, 0) DESC, nodes DESC, entries DESC",
    "time": "t0_utc",
    "recent": "t0_utc DESC",
    "duration": "t1_utc - t0_utc DESC",
}
TABLE = "precomputed table"
QUERY_TIME = "chained at query time"
_TEMPLATE_RE = re.compile(r"^[0-9a-f]{16}$")


# Which way a call is answered ------------------------------------------------------------------------


def reads_table(
    gap_s: int, merge_scope: str, filters: Sequence[str], *, pattern_key: str | None = None
) -> bool:
    """True when the precomputed table answers the call, False when it is chained at query time.

    `filters` names the Data Scope filters the call gave (pattern_key aside). A call with other
    parameters than the table's and no narrowing filter at all raises ScopeError with a suggestion.
    """
    default = gap_s == DEFAULT_GAP_S and merge_scope == DEFAULT_MERGE_SCOPE
    if default:
        return not filters
    if not filters and pattern_key is None:
        raise ScopeError(
            f"gap_s={gap_s} and merge_scope={merge_scope!r} differ from the precomputed table "
            f"(gap_s={DEFAULT_GAP_S}, merge_scope={DEFAULT_MERGE_SCOPE!r}), so the Incidents are chained "
            "at query time, and that needs a narrower Data Scope than a whole Dataset. Add a narrowing "
            "filter: pattern_key (an alert category or a template_id from list_patterns), alert_categories, "
            "pattern_ids, components, levels, sources or a time range (time_from, time_to). Or drop gap_s "
            "and merge_scope to read the precomputed table."
        )
    return False


def pattern_key_of(dataset: str, value: str | None, alert_categories: Sequence[str]) -> str | None:
    """The canonical pattern_key of a call: a BGL alert category, a template id or LIFECYCLE_KEY."""
    if value is None or not value.strip():
        return None
    key = value.strip()
    if _TEMPLATE_RE.match(key.lower()):
        return key.lower()
    if dataset == "openstack":
        if key.lower() == LIFECYCLE_KEY:
            return LIFECYCLE_KEY
        raise ScopeError(
            f"pattern_key={value!r} is not an OpenStack pattern_key. OpenStack Incidents are keyed by a "
            f"16-hex template_id (from list_patterns or count_by(group_by='pattern')), or by "
            f"{LIFECYCLE_KEY!r} for the instances whose lifecycle never completes."
        )
    if key.lower() == LIFECYCLE_KEY:
        raise ScopeError(f"{LIFECYCLE_KEY!r} is an OpenStack pattern_key; pass dataset='openstack'.")
    try:
        return validate_choice("pattern_key", key, alert_categories)
    except ScopeError as exc:
        raise ScopeError(
            f"{exc} A BGL pattern_key is an alert category (count_by(group_by='alert_category') lists "
            "them) or a 16-hex template_id from list_patterns."
        ) from None


# SQL ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    dataset: str
    part: Part
    pattern_key: str | None
    gap_s: int
    merge_scope: str
    sort: str
    limit: int

    @property
    def rel(self) -> Relation:
        return self.part.rel

    @property
    def is_bgl(self) -> bool:
        return self.dataset == "bgl"


def _host(rel: Relation, alias: str) -> sql.Composable:
    """The host of an OpenStack Log Entry: the nova service whose log file carries it (nova-api, ...)."""
    log = sql.SQL("{}.{}").format(sql.SQL(alias), rel.column("log_file"))
    return sql.SQL("coalesce(nullif(regexp_extract({log}, '^([A-Za-z0-9_-]+)', 1), ''), {log})").format(
        log=log
    )


def source_of(rel: Relation, alias: str = "e") -> sql.Composable:
    """The source of a Log Entry, as Incidents count Spread: the BGL node; for OpenStack the instance
    the Log Entry names, or its host when it names none. sql/derive/35_os_incidents.sql uses the same."""
    if rel.dataset.name == "bgl":
        return sql.SQL("{}.{}").format(sql.SQL(alias), rel.column("node"))
    instance = sql.SQL("{}.{}").format(sql.SQL(alias), rel.column("instance_ref"))
    return sql.SQL("coalesce({}, {})").format(instance, _host(rel, alias))


def _entries(call: _Call, *, filtered: bool) -> Q:
    """The Log Entries the Incidents are made of, one row each, with their source and topology."""
    rel = call.rel

    def col(concept: str) -> sql.Composable:
        return sql.SQL("e.{}").format(rel.column(concept))

    if call.is_bgl:
        topology = sql.SQL(
            "{node} AS src, {nodecard} AS nodecard, {midplane} AS midplane, {rack} AS rack, "
            "CAST(NULL AS VARCHAR) AS instance, CAST(NULL AS VARCHAR) AS host"
        ).format(node=col("node"), nodecard=col("nodecard"), midplane=col("midplane"), rack=col("rack"))
    else:
        topology = sql.SQL(
            "{src} AS src, CAST(NULL AS VARCHAR) AS nodecard, "
            "CAST(NULL AS VARCHAR) AS midplane, CAST(NULL AS VARCHAR) AS rack, {instance} AS instance, "
            "CASE WHEN {instance} IS NULL THEN {host} END AS host"
        ).format(src=source_of(rel), instance=col("instance_ref"), host=_host(rel, "e"))
    conditions = [q("{} IS NOT NULL", col("pattern_key"))]
    if not call.is_bgl:
        # The Request Failure predicate; sql/derive/35_os_incidents.sql uses the same.
        conditions.append(q("({} IN ('ERROR', 'CRITICAL') OR {} >= 500)", col("level"), col("http_status")))
    if call.pattern_key is not None:
        conditions.append(q("{} = {}", col("pattern_key"), bound(call.pattern_key)))
    if filtered:
        conditions.append(call.part.conditions(in_window=True))
        source = call.part.source
    else:
        source = q("{} AS e", rel.table)
    where = Q(
        sql.SQL(" AND ").join(c.sql for c in conditions), tuple(v for c in conditions for v in c.params)
    )
    return q(
        "SELECT {} AS entry_id, {} AS ts, {} AS epoch_utc, {} AS k, {} FROM {} WHERE {}",
        col("entry_id"),
        col("time"),
        col("epoch"),
        col("pattern_key"),
        topology,
        source,
        where,
    )


def _shape() -> sql.Composable:
    labels = dict(zip(("storm", "isolated", "one", "multi", "wide"), SHAPES, strict=True))
    return sql.SQL(
        "CASE WHEN s.nodes = 1 AND s.entries >= 100 THEN {storm} WHEN s.nodes = 1 THEN {isolated} "
        "WHEN s.midplanes >= 64 THEN {wide} WHEN s.midplanes > 1 THEN {multi} ELSE {one} END"
    ).format(**{name: literal(label) for name, label in labels.items()})


def _chained(call: _Call) -> Q:
    """The Incidents of the filtered Log Entries, chained the way the derive steps chain them."""
    per_source = call.merge_scope == "source"
    scope_src = sql.SQL("src" if per_source else "''")
    incident_id = (
        sql.SQL("CAST(row_number() OVER (PARTITION BY s.k ORDER BY s.t0_utc, s.scope_src) AS BIGINT)")
        if per_source
        else sql.SQL("s.chain")
    )
    topology = (
        sql.SQL("s.nodecards, s.midplanes, s.racks")
        if call.is_bgl
        else sql.SQL(
            "CAST(NULL AS BIGINT) AS nodecards, CAST(NULL AS BIGINT) AS midplanes, "
            "CAST(NULL AS BIGINT) AS racks"
        )
    )
    shape = _shape() if call.is_bgl else sql.SQL("CAST(NULL AS VARCHAR)")
    return q(
        """matching AS ({}),
gapped AS (
    SELECT *, {} AS scope_src,
           lag(epoch_utc) OVER (PARTITION BY k, {} ORDER BY epoch_utc, entry_id) AS previous_in_chain,
           lag(epoch_utc) OVER (PARTITION BY k, src ORDER BY epoch_utc, entry_id) AS previous_on_source
    FROM matching
),
numbered AS (
    SELECT *,
           CAST(sum(CASE WHEN previous_in_chain IS NULL OR epoch_utc - previous_in_chain > {}
                         THEN 1 ELSE 0 END)
                OVER (PARTITION BY k, scope_src ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING)
                AS BIGINT) AS chain,
           CAST(sum(CASE WHEN previous_on_source IS NULL OR epoch_utc - previous_on_source > {}
                         THEN 1 ELSE 0 END)
                OVER (PARTITION BY k, src ORDER BY epoch_utc, entry_id ROWS UNBOUNDED PRECEDING)
                AS BIGINT) AS burst
    FROM gapped
),
spread AS (
    SELECT k, scope_src, chain, min(ts) AS t0, max(ts) AS t1, min(epoch_utc) AS t0_utc,
           max(epoch_utc) AS t1_utc, count(*) AS entries, count(DISTINCT src) AS nodes,
           count(DISTINCT nodecard) AS nodecards, count(DISTINCT midplane) AS midplanes,
           count(DISTINCT rack) AS racks
    FROM numbered GROUP BY k, scope_src, chain
),
per_source AS (
    SELECT k, scope_src, chain, src, count(*) AS source_entries, count(DISTINCT burst) AS source_bursts
    FROM numbered GROUP BY k, scope_src, chain, src
),
bursts AS (
    SELECT k, scope_src, chain, CAST(sum(source_bursts) AS BIGINT) AS bursts
    FROM per_source GROUP BY k, scope_src, chain
),
top_source AS (
    SELECT k, scope_src, chain, src, source_entries FROM (
        SELECT *, row_number() OVER (PARTITION BY k, scope_src, chain ORDER BY source_entries DESC, src)
               AS rank_in_incident
        FROM per_source) ranked
    WHERE rank_in_incident = 1
),
incidents AS (
    SELECT s.k AS pattern_key, {} AS incident_id, s.t0, s.t1, s.t0_utc, s.t1_utc, s.entries, b.bursts,
           s.nodes, {}, t.src AS top_node, CAST(t.source_entries AS DOUBLE) / s.entries AS top_node_share,
           {} AS shape
    FROM spread s
    JOIN bursts b ON b.k = s.k AND b.scope_src = s.scope_src AND b.chain = s.chain
    JOIN top_source t ON t.k = s.k AND t.scope_src = s.scope_src AND t.chain = s.chain
),
sources AS (SELECT src, nodecard, midplane, rack, instance, host FROM matching)""",
        _entries(call, filtered=True),
        scope_src,
        scope_src,
        bound(call.gap_s),
        bound(call.gap_s),
        incident_id,
        topology,
        shape,
    )


def _precomputed(call: _Call) -> Q:
    """The Incidents of the precomputed table, and the Log Entries they are made of."""
    table = call.rel.incidents_table
    selected = [q("dataset = {}", bound(call.dataset))]
    if call.pattern_key is not None:
        selected.append(q("pattern_key = {}", bound(call.pattern_key)))
    where = Q(sql.SQL(" AND ").join(c.sql for c in selected), tuple(v for c in selected for v in c.params))
    if call.is_bgl:
        sources = q(
            "SELECT src, nodecard, midplane, rack, instance, host FROM ({}) AS matching",
            _entries(call, filtered=False),
        )
    else:
        # The lifecycle Request Failures are not chained Log Entries: each adds its instance.
        lifecycle = [q("dataset = {}", bound(call.dataset)), q("pattern_key = {}", bound(LIFECYCLE_KEY))]
        if call.pattern_key is not None:
            lifecycle.append(q("pattern_key = {}", bound(call.pattern_key)))
        sources = q(
            "SELECT src, nodecard, midplane, rack, instance, host FROM ({}) AS matching "
            "UNION ALL SELECT top_node, NULL, NULL, NULL, top_node, NULL FROM {} WHERE {}",
            _entries(call, filtered=False),
            table,
            Q(sql.SQL(" AND ").join(c.sql for c in lifecycle), tuple(v for c in lifecycle for v in c.params)),
        )
    return q(
        "incidents AS (SELECT pattern_key, incident_id, t0, t1, t0_utc, t1_utc, entries, bursts, nodes, "
        "nodecards, midplanes, racks, top_node, top_node_share, shape FROM {} WHERE {}), sources AS ({})",
        table,
        where,
        sources,
    )


_SHAPE_COUNTS = sql.SQL(", ").join(
    sql.SQL("count(*) FILTER (WHERE shape = {}) AS {}").format(literal(label), sql.Identifier(f"shape_{i}"))
    for i, label in enumerate(SHAPES)
)


def _statement(call: _Call, from_table: bool) -> Q:
    """The listed Incidents, each row carrying the totals over all Incidents and their Log Entries."""
    ctes = _precomputed(call) if from_table else _chained(call)
    order = sql.SQL(SORTS[call.sort] + ", pattern_key, incident_id")
    return q(
        """WITH {},
totals AS (
    SELECT count(*) AS incidents_total, coalesce(sum(entries), 0) AS entries_total,
           max(entries) AS largest_entries, max(nodes) AS widest_nodes, {}
    FROM incidents
),
spread_totals AS (
    SELECT count(DISTINCT src) AS sources_total, count(DISTINCT nodecard) AS nodecards_total,
           count(DISTINCT midplane) AS midplanes_total, count(DISTINCT rack) AS racks_total,
           count(DISTINCT instance) AS instances_total, count(DISTINCT host) AS hosts_total
    FROM sources
),
day_totals AS (
    SELECT count(DISTINCT CAST(i.t0 AS DATE)) AS incident_days,
           count(*) FILTER (WHERE i.nodes = t.widest_nodes) AS widest_incidents
    FROM incidents i CROSS JOIN totals t
)
SELECT totals.*, spread_totals.*, day_totals.*, pattern_key, incident_id, t0, t1,
       round(t1_utc - t0_utc, 3) AS duration_s, entries, bursts, nodes, nodecards, midplanes, racks,
       top_node, round(top_node_share, 4) AS top_node_share, shape
FROM totals CROSS JOIN spread_totals CROSS JOIN day_totals
LEFT JOIN (SELECT * FROM incidents ORDER BY {} LIMIT {}) AS listed ON TRUE
ORDER BY {}""",
        ctes,
        _SHAPE_COUNTS,
        order,
        bound(call.limit),
        order,
    )


# Reading the result -------------------------------------------------------------------------------------

_TOTALS = (
    "incidents_total",
    "entries_total",
    "largest_entries",
    "widest_nodes",
    *(f"shape:{label}" for label in SHAPES),
    "sources_total",
    "nodecards_total",
    "midplanes_total",
    "racks_total",
    "instances_total",
    "hosts_total",
    "incident_days",
    "widest_incidents",
)
_ROW = (
    "pattern_key",
    "incident_id",
    "t0",
    "t1",
    "duration_s",
    "entries",
    "bursts",
    "nodes",
    "nodecards",
    "midplanes",
    "racks",
    "top_node",
    "top_node_share",
    "shape",
)
BGL_COLUMNS = list(_ROW)
OS_COLUMNS = [
    "pattern_key",
    "incident_id",
    "t0",
    "t1",
    "duration_s",
    "entries",
    "bursts",
    "sources",
    "top_source",
    "top_source_share",
]
_OS_FROM = {"sources": "nodes", "top_source": "top_node", "top_source_share": "top_node_share"}


def _number(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _read(call: _Call, rows: Sequence[Sequence[Any]]) -> tuple[dict[str, Any], list[list[Any]]]:
    totals: dict[str, Any] = dict(zip(_TOTALS, rows[0][: len(_TOTALS)], strict=True)) if rows else {}
    listed: list[list[Any]] = []
    for row in rows:
        incident = dict(zip(_ROW, row[len(_TOTALS) :], strict=True))
        if incident["incident_id"] is None:  # no Incident: the LEFT JOIN gave the totals alone
            continue
        incident["duration_s"] = _number(incident["duration_s"])
        if call.is_bgl:
            listed.append([incident[c] for c in BGL_COLUMNS])
        else:
            listed.append([incident[_OS_FROM.get(c, c)] for c in OS_COLUMNS])
    return totals, listed


def _summary(call: _Call, from_table: bool, totals: dict[str, Any]) -> dict[str, Any]:
    def count(name: str) -> int:
        return int(totals.get(name) or 0)

    summary: dict[str, Any] = {
        "dataset": call.dataset,
        "pattern_key": call.pattern_key,
        "gap_s": call.gap_s,
        "merge_scope": call.merge_scope,
        "sort": call.sort,
        "computed": TABLE if from_table else QUERY_TIME,
        "entries_total": count("entries_total"),
        "incidents_total": count("incidents_total"),
        "largest_incident_entries": count("largest_entries"),
        "incident_days": count("incident_days"),
    }
    if call.is_bgl:
        summary |= {
            "nodes_total": count("sources_total"),
            "nodecards_total": count("nodecards_total"),
            "midplanes_total": count("midplanes_total"),
            "racks_total": count("racks_total"),
            "widest_incident_nodes": count("widest_nodes"),
            "widest_incidents": count("widest_incidents"),
            "incidents_by_shape": {
                label: count(f"shape:{label}") for label in SHAPES if count(f"shape:{label}")
            },
        }
    else:
        summary |= {
            "sources_total": count("sources_total"),
            "instances_total": count("instances_total"),
            "hosts_total": count("hosts_total"),
            "widest_incident_sources": count("widest_nodes"),
            "widest_incidents": count("widest_incidents"),
        }
    return summary


def _notes(call: _Call, from_table: bool, summary: dict[str, Any]) -> list[str]:
    notes = [
        f"An Incident is a maximal chain of Log Entries of one Pattern where each follows the previous one "
        f"within {call.gap_s} s (gaps on the UTC clock), merged "
        + ("across every source" if call.merge_scope == "all" else "per source (a Burst)")
        + ". entries_total counts the Log Entries of all listed Incidents, incidents_total the Incidents: "
        "quote both, because one source can write thousands of Log Entries about one problem.",
        "incident_days counts the distinct calendar days the Incidents start on, by t0 as listed"
        + (" (BGL ts as logged, US Pacific time)" if call.is_bgl else " (UTC)")
        + "; widest_incidents counts the Incidents that reach the widest Spread, widest_incident_"
        + ("nodes." if call.is_bgl else "sources."),
    ]
    if from_table:
        notes.append(
            f"Read from the precomputed Incident table (gap_s={DEFAULT_GAP_S}, merge_scope="
            f"'{DEFAULT_MERGE_SCOPE}'); any filter besides pattern_key, or other parameters, chains at query "
            "time instead."
        )
    else:
        notes.append(
            "Chained at query time over the Log Entries of this Data Scope only: an Incident that runs past "
            "the edge of the time range is cut at the edge."
        )
    if call.is_bgl:
        notes.append(
            "Spread: nodes, nodecards, midplanes and racks per Incident; the *_total values in summary "
            "count distinct ones over all Log Entries of the listed Incidents. Shape: 'single-node storm' "
            "(one node, at least 100 Log Entries), 'single-node isolated', 'one midplane', "
            "'multi-midplane', 'machine-wide' (64 or more of the 128 midplanes)."
        )
    else:
        notes.append(
            "OpenStack Incidents are Request Failures: Log Entries at ERROR or CRITICAL, or with an HTTP "
            "status of 500 or more, chained per Pattern. A source is the instance a Log Entry names, or "
            "its host (the nova service whose log carries it: nova-api, nova-compute, nova-scheduler) "
            "when it names none. OpenStack Incidents have no Shape; the Shape labels describe BGL topology."
        )
        notes.append(
            f"pattern_key {LIFECYCLE_KEY!r}: one Request Failure per instance whose build or teardown "
            "started and never completed, holding every Log Entry that names it. It is in the precomputed "
            "table only."
            if from_table
            else f"The lifecycle Request Failures (pattern_key {LIFECYCLE_KEY!r}) are not chained Log "
            "Entries and are left out here; call with dataset='openstack' and no filter but pattern_key "
            "to read them."
        )
    if summary["incidents_total"] == 0:
        notes.append(
            "No Incidents in this Data Scope. Check pattern_key against list_patterns (template ids) or "
            "count_by(group_by='alert_category') (BGL alert categories), or widen the time range."
        )
    return notes


# Similar Incidents (T16, #7) -----------------------------------------------------------------------------

SIMILAR_EXTRA = ["jaccard", "shared_patterns", "signature_patterns", "same_component"]
_REF = ("pattern_key", "incident_id", "t0", "t1", "entries", "nodes", "shape")
_SIMILAR_ORDER = (
    "jaccard DESC, same_component DESC, same_shape DESC, spread_gap, t0_utc DESC, pattern_key, incident_id"
)


def check_similar_to(
    similar_to: int | None, pattern_key: str | None, gap_s: int, merge_scope: str, filters: Sequence[str]
) -> None:
    """Raise ScopeError when a `similar_to` call cannot name its reference Incident in the table."""
    if similar_to is None:
        return
    if pattern_key is None:
        raise ScopeError(
            "similar_to is the incident_id of one Incident of pattern_key, because incident ids count "
            "within one Pattern: pass pattern_key too, for example pattern_key='KERNTERM', similar_to=98, "
            "with the pattern_key and incident_id of a row of list_incidents."
        )
    if pattern_key == LIFECYCLE_KEY:
        raise ScopeError(
            f"similar_to compares the Patterns seen on an Incident's sources while it lasts; the "
            f"{LIFECYCLE_KEY!r} Request Failures are whole instance lifecycles, not chained Log Entries. "
            "Pass the template_id of a chained Request Failure instead."
        )
    if gap_s != DEFAULT_GAP_S or merge_scope != DEFAULT_MERGE_SCOPE or filters:
        given = ", ".join(filters) or "gap_s or merge_scope"
        raise ScopeError(
            f"similar_to ranks the Incidents of the precomputed table (gap_s={DEFAULT_GAP_S}, merge_scope="
            f"'{DEFAULT_MERGE_SCOPE}'), so it takes no other parameters; drop {given}."
        )


def _members(call: _Call, incident: str) -> Q:
    """The Log Entries an Incident of the table is made of: its Pattern's entries during it (BGL, or the
    failing ones for OpenStack), because an Incident is a maximal chain merged across every source."""
    rel = call.rel
    failing = (
        Q(sql.SQL(""))
        if call.is_bgl
        else q(
            " AND ({} IN ('ERROR', 'CRITICAL') OR {} >= 500)",
            sql.SQL("e.{}").format(rel.column("level")),
            sql.SQL("e.{}").format(rel.column("http_status")),
        )
    )
    return q(
        "JOIN {} AS e ON e.{} = {}.pattern_key AND e.{} >= {}.t0_utc AND e.{} <= {}.t1_utc{}",
        rel.table,
        rel.column("pattern_key"),
        sql.SQL(incident),
        rel.column("epoch"),
        sql.SQL(incident),
        rel.column("epoch"),
        sql.SQL(incident),
        failing,
    )


def _on_sources(call: _Call, sources: str) -> Q:
    """The Log Entries on the sources of `sources` (columns src, t0_utc, t1_utc) while the Incident lasts."""
    rel = call.rel
    return q(
        "JOIN {} AS e ON {} = {}.src AND e.{} >= {}.t0_utc AND e.{} <= {}.t1_utc WHERE e.{} IS NOT NULL",
        rel.table,
        source_of(rel),
        sql.SQL(sources),
        rel.column("epoch"),
        sql.SQL(sources),
        rel.column("epoch"),
        sql.SQL(sources),
        rel.column("pattern_key"),
    )


def _reference_statement(call: _Call, similar_to: int) -> Q:
    """The reference Incident, one row per Pattern of its signature."""
    return q(
        """WITH ref AS (
    SELECT * FROM {} WHERE dataset = {} AND pattern_key = {} AND incident_id = {}
),
ref_sources AS (SELECT DISTINCT {} AS src, r.t0_utc, r.t1_utc FROM ref AS r {})
SELECT r.pattern_key, r.incident_id, r.t0, r.t1, r.entries, r.nodes, r.shape, sig.p
FROM ref AS r LEFT JOIN (SELECT DISTINCT e.{} AS p FROM ref_sources AS s {}) AS sig ON TRUE
ORDER BY sig.p""",
        call.rel.incidents_table,
        bound(call.dataset),
        bound(call.pattern_key),
        bound(similar_to),
        source_of(call.rel),
        _members(call, "r"),
        call.rel.column("pattern_key"),
        _on_sources(call, "s"),
    )


def _similar_statement(call: _Call, similar_to: int) -> Q:
    """Earlier Incidents ranked by the Jaccard similarity of their signatures to the reference's.

    A signature is the set of Patterns seen on an Incident's sources while it lasts. Candidates are
    the earlier Incidents whose own Pattern is in the reference signature (blocking); every other
    Incident would need its whole signature computed, which over 42,199 BGL Incidents takes about 15 s.
    """
    rel = call.rel
    return q(
        """WITH incidents AS (SELECT * FROM {} WHERE dataset = {} AND pattern_key <> {}),
ref AS (SELECT * FROM incidents WHERE pattern_key = {} AND incident_id = {}),
ref_entries AS (SELECT {} AS src, e.{} AS component, r.t0_utc, r.t1_utc FROM ref AS r {}),
ref_sources AS (SELECT DISTINCT src, t0_utc, t1_utc FROM ref_entries),
ref_component AS (
    SELECT component FROM ref_entries GROUP BY component ORDER BY count(*) DESC, component LIMIT 1
),
ref_sig AS (SELECT DISTINCT e.{} AS p FROM ref_sources AS s {}),
ref_size AS (SELECT count(*) AS n FROM ref_sig),
cand AS (
    SELECT c.* FROM incidents AS c CROSS JOIN ref AS r
    WHERE c.t0_utc < r.t0_utc AND c.pattern_key IN (SELECT p FROM ref_sig)
),
cand_entries AS (
    SELECT c.pattern_key AS k, c.incident_id AS id, c.t0_utc, c.t1_utc, {} AS src, e.{} AS component
    FROM cand AS c {}
),
cand_sources AS (SELECT DISTINCT k, id, t0_utc, t1_utc, src FROM cand_entries),
cand_component AS (
    SELECT k, id, component FROM (
        SELECT k, id, component,
               row_number() OVER (PARTITION BY k, id ORDER BY count(*) DESC, component) AS rnk
        FROM cand_entries GROUP BY k, id, component) AS ranked
    WHERE rnk = 1
),
cand_sig AS (SELECT DISTINCT s.k, s.id, e.{} AS p FROM cand_sources AS s {}),
scored AS (
    SELECT k, id, count(*) AS sig_n, count(*) FILTER (WHERE p IN (SELECT p FROM ref_sig)) AS shared
    FROM cand_sig GROUP BY k, id
),
ranked AS (
    SELECT c.pattern_key, c.incident_id, c.t0, c.t1, c.t0_utc, c.t1_utc, c.entries, c.bursts, c.nodes,
           c.nodecards, c.midplanes, c.racks, c.top_node, c.top_node_share, c.shape,
           CAST(s.shared AS DOUBLE) / (s.sig_n + z.n - s.shared) AS jaccard, s.shared, s.sig_n,
           coalesce(cc.component = rc.component, FALSE) AS same_component,
           coalesce(c.shape, '') = coalesce(r.shape, '') AS same_shape,
           abs(c.nodes - r.nodes) AS spread_gap
    FROM cand AS c
    JOIN scored AS s ON s.k = c.pattern_key AND s.id = c.incident_id
    LEFT JOIN cand_component AS cc ON cc.k = c.pattern_key AND cc.id = c.incident_id
    CROSS JOIN ref AS r CROSS JOIN ref_size AS z
    LEFT JOIN ref_component AS rc ON TRUE
)
SELECT (SELECT count(*) FROM ranked) AS scored_total, (SELECT count(*) FROM cand) AS candidates_total,
       l.pattern_key, l.incident_id, l.t0, l.t1, round(l.t1_utc - l.t0_utc, 3) AS duration_s, l.entries,
       l.bursts, l.nodes, l.nodecards, l.midplanes, l.racks, l.top_node, round(l.top_node_share, 4),
       l.shape, round(l.jaccard, 4), l.shared, l.sig_n, l.same_component
FROM (SELECT 1 AS one) AS o
LEFT JOIN (SELECT * FROM ranked ORDER BY {} LIMIT {}) AS l ON TRUE
ORDER BY {}""",
        rel.incidents_table,
        bound(call.dataset),
        bound(LIFECYCLE_KEY),
        bound(call.pattern_key),
        bound(similar_to),
        source_of(rel),
        rel.column("component"),
        _members(call, "r"),
        rel.column("pattern_key"),
        _on_sources(call, "s"),
        source_of(rel),
        rel.column("component"),
        _members(call, "c"),
        rel.column("pattern_key"),
        _on_sources(call, "s"),
        sql.SQL(_SIMILAR_ORDER),
        bound(call.limit),
        sql.SQL("l." + _SIMILAR_ORDER.replace(", ", ", l.")),
    )


def _similar_rows(call: _Call, rows: Sequence[Sequence[Any]]) -> tuple[dict[str, int], list[list[Any]]]:
    totals = {"scored_total": int(rows[0][0] or 0), "candidates_total": int(rows[0][1] or 0)} if rows else {}
    names = (*_ROW, "jaccard", "shared_patterns", "signature_patterns", "same_component")
    listed: list[list[Any]] = []
    for row in rows:
        incident = dict(zip(names, row[2:], strict=True))
        if incident["incident_id"] is None:
            continue
        incident["duration_s"] = _number(incident["duration_s"])
        incident["jaccard"] = _number(incident["jaccard"])
        columns = BGL_COLUMNS if call.is_bgl else OS_COLUMNS
        keys = columns if call.is_bgl else [_OS_FROM.get(c, c) for c in columns]
        listed.append([incident[k] for k in [*keys, *SIMILAR_EXTRA]])
    return totals, listed


def _similar_notes(
    call: _Call, reference: dict[str, Any], signature: list[str], totals: dict[str, int]
) -> list[str]:
    where = "nodes" if call.is_bgl else "sources (instances, or hosts for Log Entries that name none)"
    notes = [
        f"Similar Incidents: earlier Incidents ranked by the Jaccard similarity of their signatures to the "
        f"signature of {call.pattern_key} Incident {reference['incident_id']}. A signature is the set of "
        f"Patterns seen on an Incident's {where} from its first to its last Log Entry; Jaccard is shared "
        "Patterns over all Patterns of both. Ties are broken by the same dominant Component, then the same "
        "Shape, then the closest number of sources, then the most recent. It is an exact set comparison, "
        "never a BM25 or text-relevance sort, and uses no embeddings.",
        f"The reference signature has {len(signature)} Patterns. Candidates are the earlier Incidents whose "
        f"own Pattern is in it: {totals.get('candidates_total', 0):,} of them, all scored "
        f"({totals.get('scored_total', 0):,}); an Incident of another Pattern can share Patterns with the "
        "reference and is not scored. Read from the precomputed Incident table "
        f"(gap_s={DEFAULT_GAP_S}, merge_scope='{DEFAULT_MERGE_SCOPE}').",
        "A look-alike is a Hypothesis that the Incidents share a cause; the Jaccard value is an Observed "
        "Fact about the Pattern sets.",
    ]
    if not call.is_bgl:
        notes.append(
            f"The {LIFECYCLE_KEY!r} Request Failures are not compared: they are not chained Log Entries."
        )
    if totals.get("scored_total", 0) == 0:
        notes.append(
            "No earlier Incident shares a Pattern with the reference signature: nothing similar came before."
        )
    return notes


def _similar(call: _Call, similar_to: int, role: Role, scope: DataScope) -> ToolOutcome:
    found = run(_reference_statement(call, similar_to), role)
    if not found.rows:
        raise ScopeError(
            f"There is no {call.dataset} Incident with pattern_key={call.pattern_key!r} and incident_id="
            f"{similar_to}. Call list_incidents(dataset={call.dataset!r}, pattern_key={call.pattern_key!r}) "
            "for its incident ids."
        )
    reference = dict(zip(_REF, found.rows[0][: len(_REF)], strict=True))
    if call.dataset != "bgl":
        reference["sources"] = reference.pop("nodes")
        reference.pop("shape")
    signature = [str(row[len(_REF)]) for row in found.rows if row[len(_REF)] is not None]
    result = run(_similar_statement(call, similar_to), role)
    totals, rows = _similar_rows(call, result.rows)
    summary: dict[str, Any] = {
        "dataset": call.dataset,
        "pattern_key": call.pattern_key,
        "similar_to": similar_to,
        "reference": reference,
        "reference_patterns": signature,
        "candidates_total": totals.get("candidates_total", 0),
        "scored_total": totals.get("scored_total", 0),
        "gap_s": call.gap_s,
        "merge_scope": call.merge_scope,
        "computed": TABLE,
    }
    total = summary["scored_total"]
    return ToolOutcome(
        result=QueryResult.combine(found, result, total=total, rows=rows),
        scope=scope,
        columns=(BGL_COLUMNS if call.is_bgl else OS_COLUMNS) + SIMILAR_EXTRA,
        summary=summary,
        notes=_similar_notes(call, reference, signature, totals),
        rows=rows,
        total_matches=total,
        truncation_note=(
            f"Showing the {len(rows)} most similar of {total:,} scored earlier Incidents; "
            "scored_total in summary counts all of them."
        ),
    )


# The tool --------------------------------------------------------------------------------------------------


def register(server: MCPServer) -> None:
    """Register `list_incidents` on `server`."""

    @evidence_tool(server, title="Incidents with Spread and Shape, next to the Log Entry counts")
    def list_incidents(
        role: RoleParam,
        dataset: Annotated[
            DatasetName,
            Field(description="'bgl' or 'openstack': one Dataset per call."),
        ],
        pattern_key: Annotated[
            str | None,
            Field(
                description="Only the Incidents of this Pattern: a BGL alert category such as 'KERNDTLB' "
                "(the alert_category of count_by and search_logs), a 16-hex template_id from list_patterns "
                f"(either Dataset), or {LIFECYCLE_KEY!r} (OpenStack instances whose lifecycle never "
                "completes). Omit it for every Pattern.",
                max_length=64,
            ),
        ] = None,
        gap_s: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_GAP_S,
                description=f"Seconds within which the next Log Entry continues an Incident (default "
                f"{DEFAULT_GAP_S}). Counts are stable from 60 to 600 s; a value other than "
                f"{DEFAULT_GAP_S} needs a narrowing filter.",
            ),
        ] = DEFAULT_GAP_S,
        merge_scope: Annotated[
            MergeScope,
            Field(
                description="'all' (default) chains across every source; 'source' chains per BGL node or "
                "OpenStack source, so each Incident is one Burst. 'source' needs a narrowing filter."
            ),
        ] = "all",
        sort: Annotated[
            Sort,
            Field(
                description="'entries' (most Log Entries first, default), 'spread' (most midplanes, then "
                "nodes), 'time' (oldest first), 'recent' (newest first) or 'duration' (longest first)."
            ),
        ] = "entries",
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_LIMIT,
                description=f"How many Incidents to list (default {DEFAULT_LIMIT}, at most {MAX_LIMIT}).",
            ),
        ] = DEFAULT_LIMIT,
        query: QueryParam = None,
        time_from: UtcTimeFromParam = None,
        time_to: UtcTimeToParam = None,
        components: ComponentsParam = None,
        levels: LevelsParam = None,
        sources: SourcesParam = None,
        alert_categories: AlertCategoriesParam = None,
        pattern_ids: PatternIdsParam = None,
        family_id: FamilyIdParam = None,
        similar_to: Annotated[
            int | None,
            Field(
                ge=1,
                description="The incident_id of one Incident of pattern_key (both from a list_incidents "
                "row): list the earlier Incidents most like it, ranked by the Jaccard similarity of the "
                "Pattern sets seen on their sources while they lasted, then same Component, then Spread. "
                "Needs pattern_key and no other filter; sort is then ignored.",
            ),
        ] = None,
    ) -> ToolOutcome:
        """Incidents: Log Entries of one Pattern chained in time, with Spread and Shape, next to
        the Log Entry counts.

        Purpose: tell one source shouting thousands of lines from a problem that recurs or spreads.
        An Incident is a maximal chain of Log Entries of one Pattern where each follows the previous
        one within gap_s seconds (default 300; counts are stable across the band 60 to 600 s),
        merged across sources. Quote entries_total and incidents_total together: they are different
        units. For BGL each Incident has Spread (nodes, nodecards, midplanes, racks, top_node and
        top_node_share) and a Shape (single-node storm, single-node isolated, one midplane,
        multi-midplane, machine-wide). OpenStack Incidents are Request Failures (ERROR or CRITICAL
        lines and HTTP 5xx responses, plus instances whose lifecycle never completes), with sources
        (instances, or hosts for lines that name no instance) as Spread and no Shape.
        similar_to: with pattern_key, the incident_id of a reference Incident: the rows are then
        the earlier Incidents most like it (exact Jaccard similarity of the Pattern sets seen on
        their sources while they lasted, then same Component, then Spread; never a BM25 sort), with
        jaccard, shared_patterns, signature_patterns and same_component columns.
        pattern_key: a BGL alert category (the alert_category value of count_by and search_logs,
        for example 'KERNDTLB'), or a template_id from list_patterns (choose Patterns there, then
        pass one here), or 'lifecycle-incomplete' for OpenStack.
        The default call (gap_s=300, merge_scope='all', no filter but pattern_key) reads the
        precomputed table; any other call chains the filtered Log Entries at query time, and other
        gap_s or merge_scope values need a narrowing filter (pattern_key, alert_categories,
        pattern_ids, components, levels, sources, query or a time range).
        Inputs: role, dataset, and optional pattern_key, gap_s, merge_scope, sort (entries, spread,
        time, recent, duration), limit (default 20, at most 100), query, time_from, time_to,
        components, levels, sources, alert_categories, pattern_ids, family_id and similar_to.
        Outputs: the Evidence envelope. rows: one per Incident with pattern_key, incident_id, t0, t1,
        duration_s, entries, bursts and the Spread columns. summary: entries_total, incidents_total,
        incident_days (distinct days of t0), widest_incidents (Incidents at the widest Spread), the
        Spread totals (BGL nodes_total, nodecards_total, midplanes_total, racks_total and
        incidents_by_shape; OpenStack sources_total, instances_total, hosts_total) and computed
        (precomputed table or chained at query time); with similar_to, the reference Incident, its
        reference_patterns, candidates_total and scored_total. total_matches counts Incidents.
        """
        datasets, _ = choose_datasets(dataset, None, alert_categories)
        facts = dataset_facts(dataset, role)
        key = pattern_key_of(dataset, pattern_key, list(facts.alert_categories))
        given = [
            name
            for name, value in (
                ("query", query),
                ("time_from", time_from),
                ("time_to", time_to),
                ("components", components),
                ("levels", levels),
                ("sources", sources),
                ("alert_categories", alert_categories),
                ("pattern_ids", pattern_ids),
                ("family_id", family_id),
            )
            if value
        ]
        check_similar_to(similar_to, key, gap_s, merge_scope, given)
        from_table = reads_table(gap_s, merge_scope, given, pattern_key=key)
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
        )
        scope = prepared.scope.model_copy(
            update={
                "filters": ScopeFilters.of(
                    **prepared.scope.filters.model_dump(exclude_none=True),
                    **({"pattern_key": key} if key is not None else {}),
                    gap_s=gap_s,
                    merge_scope=merge_scope,
                )
            }
        )
        call = _Call(dataset, prepared.parts[0], key, gap_s, merge_scope, sort, limit)
        if similar_to is not None:
            similar_scope = scope.model_copy(
                update={"filters": ScopeFilters.of(**scope.filters.model_dump(), similar_to=similar_to)}
            )
            return _similar(call, similar_to, role, similar_scope)
        result = run(_statement(call, from_table), role)
        totals, rows = _read(call, result.rows)
        summary = _summary(call, from_table, totals)
        total = summary["incidents_total"]
        truncation = (
            f"Showing {len(rows)} of {total:,} Incidents, sorted by {sort}; entries_total and "
            "incidents_total in summary cover all of them."
        )
        return ToolOutcome(
            result=QueryResult.combine(result, total=total, rows=rows),
            scope=scope,
            columns=BGL_COLUMNS if call.is_bgl else OS_COLUMNS,
            summary=summary,
            notes=_notes(call, from_table, summary),
            rows=rows,
            total_matches=total,
            truncation_note=truncation,
        )
