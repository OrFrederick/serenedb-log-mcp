"""A small, fixed investigation for the export and replay tests: an Evidence Ledger and three answers.

The records are built by hand through the `Evidence` model, so they always satisfy the Evidence
contract, and they hold synthetic rows (no Log Entry text from the Loghub corpora is committed). The
numbers follow the #5 worked investigation closely enough to read naturally.

- E1 search_logs (engineer): 12 of 152,734 matching Log Entries, so tables show the first 10 rows.
- E2 get_context (engineer): 3 Log Entries around one anchor.
- E3 count_by (team_lead): Log Entries per BGL alert category.
- E4 trend (team_lead): Log Entries per day.
- E5 count_by (manager): Log Entries per Dataset over the whole Base Corpus.
- E6 count_by (engineer): Evidence no answer cites, produced for the Engineer.
"""

from __future__ import annotations

from typing import Any

from serenedb_log_mcp.evidence import DataScope, Evidence, Role, ScopeFilters
from serenedb_log_mcp.export import Answer

SEARCH_TOTAL = 152_734
SEARCH_ROWS = 12

_SEARCH_SQL = [
    'SELECT count(*) FROM "slm"."bgl_entry_idx" AS e WHERE e."message" @@ websearch_to_tsquery(%s)',
    'SELECT e."entry_id", e."ts", e."node", e."component", e."level", e."alert_tag", e."message"\n'
    'FROM "slm"."bgl_entry_idx" AS e WHERE e."message" @@ websearch_to_tsquery(%s)\n'
    'ORDER BY e."epoch_utc", e."entry_id" LIMIT %s',
]
_COUNT_SQL = (
    "WITH matching AS (\n"
    "    SELECT 'bgl' AS dataset, coalesce(e.\"alert_tag\", '(non-alert)') AS k "
    'FROM "slm"."bgl_entry" AS e WHERE e."ts" >= %s AND e."ts" < %s\n'
    ")\n"
    "SELECT dataset, k, count(*) AS n FROM matching GROUP BY dataset, k ORDER BY n DESC LIMIT %s"
)


def _search_rows() -> list[list[Any]]:
    rows = []
    for i in range(SEARCH_ROWS):
        rows.append(
            [
                1_000 + i,
                f"2005-06-11T17:32:{i:02d}",
                "R30-M0-N9-C:J16-U01",
                "KERNEL",
                "FATAL",
                "KERNDTLB",
                f"synthetic **data TLB error** interrupt | sample {i}",
            ]
        )
    return rows


def records() -> list[dict[str, Any]]:
    """The fixture ledger as the Evidence Ledger stores it: one JSON-ready dict per record."""
    built = [
        Evidence(
            evidence_id="E1",
            tool="search_logs",
            role=Role.ENGINEER,
            arguments={
                "dataset": "bgl",
                "query": "data TLB error",
                "mode": "all_words",
                "time_from": None,
                "time_to": None,
                "sort": "time",
                "limit": SEARCH_ROWS,
            },
            sql=list(_SEARCH_SQL),
            params=[["data TLB error"], ["data TLB error", SEARCH_ROWS]],
            data_scope=DataScope(
                datasets=["bgl"], filters=ScopeFilters.of(query="data TLB error", mode="all_words")
            ),
            total_matches=SEARCH_TOTAL,
            returned=SEARCH_ROWS,
            truncated=True,
            query_ms=412.5,
            columns=["entry_id", "ts", "node", "component", "level", "alert_tag", "message"],
            rows=_search_rows(),
            summary={
                "entries_total": SEARCH_TOTAL,
                "distinct_sources": 541,
                "first_seen": "2005-06-11T17:32:00",
                "top_components": {"KERNEL": SEARCH_TOTAL},
            },
            notes=["Truncated: 12 of 152,734 matches returned."],
        ),
        Evidence(
            evidence_id="E2",
            tool="get_context",
            role=Role.ENGINEER,
            arguments={"dataset": "bgl", "entry_id": 1_000, "before": 1, "after": 1, "scope": "auto"},
            sql=['SELECT e."entry_id", e."ts", e."message" FROM "slm"."bgl_entry" AS e WHERE e."node" = %s'],
            params=[["R30-M0-N9-C:J16-U01"]],
            data_scope=DataScope(
                datasets=["bgl"],
                time_from="2005-06-11T17:31:00",
                time_to="2005-06-11T17:33:00",
                filters=ScopeFilters.of(node="R30-M0-N9-C:J16-U01"),
            ),
            total_matches=3,
            returned=3,
            truncated=False,
            query_ms=8.0,
            columns=["entry_id", "ts", "message", "anchor"],
            rows=[
                [999, "2005-06-11T17:31:59", "synthetic instruction cache parity error", False],
                [1_000, "2005-06-11T17:32:00", "synthetic data TLB error interrupt", True],
                [1_001, "2005-06-11T17:32:01", "synthetic <machine check> interrupt", False],
            ],
            summary={"before_returned": 1, "after_returned": 1},
        ),
        Evidence(
            evidence_id="E3",
            tool="count_by",
            role=Role.TEAM_LEAD,
            arguments={"group_by": "alert_category", "dataset": "bgl", "top": 3},
            sql=[_COUNT_SQL],
            params=[["2005-06-03T15:42:50", "2006-01-04T08:00:06", 3]],
            data_scope=DataScope(
                datasets=["bgl"],
                time_from="2005-06-03T15:42:50",
                time_to="2006-01-04T08:00:06",
                filters=ScopeFilters.of(group_by="alert_category"),
            ),
            total_matches=41,
            returned=3,
            truncated=True,
            query_ms=230.25,
            columns=["dataset", "group", "count", "share_pct"],
            rows=[
                ["bgl", "(non-alert)", 4_399_503, 92.66],
                ["bgl", "KERNDTLB", 152_734, 3.22],
                ["bgl", "KERNSTOR", 63_491, 1.34],
            ],
            summary={"groups_total": 41, "entries_total": 4_747_963, "other_count": 132_235},
        ),
        Evidence(
            evidence_id="E4",
            tool="trend",
            role=Role.TEAM_LEAD,
            arguments={"bucket": "1 day", "alert_categories": ["KERNDTLB"], "time_from": "2005-06-10"},
            sql=["SELECT bucket, count(*) FROM t WHERE alert_tag = ANY(%s::VARCHAR[]) GROUP BY 1"],
            params=[[["KERNDTLB"]]],
            data_scope=DataScope(
                datasets=["bgl"],
                time_from="2005-06-10T00:00:00",
                time_to="2005-06-13T00:00:00",
                filters=ScopeFilters.of(alert_categories=["KERNDTLB"], bucket="1 day"),
            ),
            total_matches=3,
            returned=3,
            truncated=False,
            query_ms=95.0,
            columns=["bucket", "(all)"],
            rows=[["2005-06-10T00:00:00", 12], ["2005-06-11T00:00:00", 152_182], ["2005-06-12T00:00:00", 0]],
            summary={"buckets": 3, "entries_total": 152_194},
        ),
        Evidence(
            evidence_id="E5",
            tool="count_by",
            role=Role.MANAGER,
            arguments={"group_by": "dataset", "dataset": None, "top": 25},
            sql=[_COUNT_SQL],
            params=[["2005-06-03T15:42:50", "2017-05-17T12:02:36", 25]],
            data_scope=DataScope(datasets=["bgl", "openstack"], filters=ScopeFilters.of(group_by="dataset")),
            total_matches=2,
            returned=2,
            truncated=False,
            query_ms=310.0,
            columns=["dataset", "group", "count", "share_pct"],
            rows=[["bgl", "bgl", 4_747_963, 95.81], ["openstack", "openstack", 207_820, 4.19]],
            summary={"groups_total": 2, "entries_total": 4_955_783},
        ),
        Evidence(
            evidence_id="E6",
            tool="count_by",
            role=Role.ENGINEER,
            arguments={"group_by": "level", "dataset": "bgl", "top": 2},
            sql=[_COUNT_SQL],
            params=[["2005-06-03T15:42:50", "2006-01-04T08:00:06", 2]],
            data_scope=DataScope(datasets=["bgl"], filters=ScopeFilters.of(group_by="level")),
            total_matches=2,
            returned=2,
            truncated=False,
            query_ms=120.0,
            columns=["dataset", "group", "count"],
            rows=[["bgl", "INFO", 3_735_179], ["bgl", "FATAL", 855_501]],
            summary={"groups_total": 2},
        ),
    ]
    return [record.model_dump(mode="json") for record in built]


ENGINEER_ANSWER = """\
## Observed Facts
- The search matched 152,734 Log Entries [E1]; node R30-M0-N9-C:J16-U01 wrote the examples [E1].
- Just before the anchor the node logged an instruction cache parity error [E2].

## Hypotheses
- The node's memory hardware is failing; a parity error just before is consistent with that.

## Unknowns
- Whether the job on that node failed: these logs hold no job records.

## Next Step
- Run get_context on entry 1000 with before=50 to see the hour before.
"""

TEAM_LEAD_ANSWER = """\
## Observed Facts
- KERNDTLB is the largest alert category with 152,734 Log Entries [E3], and 152,182 of them fall on
  2005-06-11 [E4].
- The examples come from one node [E1].

## Hypotheses
- One looping node explains most of the category, not a fleet-wide problem.

## Unknowns
- Which Team owns the node: the Ownership Table maps Components, not nodes.

## Next Step
- Investigate KERNDTLB on R30-M0-N9-C:J16-U01 first: it holds most of the category.
"""

MANAGER_ANSWER = """\
## Observed Facts
- BGL holds 4,747,963 of the 4,955,783 Log Entries in the Base Corpus [E5].
- KERNDTLB is the largest alert category with 152,734 Log Entries [E3].

## Hypotheses
- The ranking could change if Incidents rather than Log Entries were counted.

## Unknowns
- Cost, downtime and customer impact are not measured by these logs.

## Next Step
- Ask the hardware Team to check the one node behind KERNDTLB before anything else.
"""

TITLE = "KERNDTLB storm on one node"


def answers() -> list[Answer]:
    """The three answers, one per Role, in the order the investigation was handed on."""
    return [
        Answer(role=Role.ENGINEER, question="What failed on R30-M0-N9-C:J16-U01?", answer=ENGINEER_ANSWER),
        Answer(role=Role.TEAM_LEAD, question="Which alert categories recur?", answer=TEAM_LEAD_ANSWER),
        Answer(role=Role.MANAGER, question="What deserves attention first?", answer=MANAGER_ANSWER),
    ]
