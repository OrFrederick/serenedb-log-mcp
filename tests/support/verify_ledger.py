"""A fixture Evidence Ledger for the answer checks (T12), built through the real Evidence contract.

The records have the shapes the real tools produce (count_by, search_logs, trend, describe_corpus,
get_context), plus `list_incidents` and `top_patterns` records shaped after plan section 2.2, because
those tools (T27, T28) do not exist yet and the checks match them by tool name.

The numbers follow the KERNDTLB story of the worked investigation (#5): 152,734 Log Entries in the
category, 152,182 of them in one single-node Incident.
"""

from __future__ import annotations

from typing import Any

from serenedb_log_mcp.evidence import Evidence

KERNDTLB = 152_734
KERNSTOR = 63_491
APPSEV = 49_651
ALERT_TOTAL = 348_460
TOP_N_SUM = KERNDTLB + KERNSTOR + APPSEV  # 265,876, returned by count_by as summary.top_n_sum
OTHER_COUNT = ALERT_TOTAL - TOP_N_SUM  # 82,584
STORM_ENTRIES = 152_182
INCIDENT_ENTRIES = (STORM_ENTRIES, 400, 152)  # adds up to KERNDTLB

COUNT_BY_COLUMNS = [
    "dataset",
    "alert_category",
    "count",
    "share_pct",
    "distinct_sources",
    "first_seen",
    "last_seen",
    "prev_period",
    "median_week",
    "expected",
    "baseline_basis",
    "baseline_flag",
]


def _evidence(evidence_id: str, tool: str, **fields: Any) -> dict[str, Any]:
    fields.setdefault("role", "engineer")
    fields.setdefault("data_scope", {"datasets": ["bgl"]})
    fields.setdefault("returned", len(fields.get("rows", [])))
    fields.setdefault("total_matches", fields["returned"])
    fields.setdefault("truncated", fields["returned"] < fields["total_matches"])
    fields.setdefault("query_ms", 41.7)
    return Evidence(evidence_id=evidence_id, tool=tool, **fields).model_dump(mode="json")


def ledger_records() -> list[dict[str, Any]]:
    """E1 count_by, E2 search_logs, E3 list_incidents, E4 trend, E5 describe_corpus,
    E6 search_logs over OpenStack (numbers inside the message text), E7 get_context, E8 top_patterns."""
    return [
        _evidence(
            "E1",
            "count_by",
            role="team_lead",
            arguments={"group_by": "alert_category", "dataset": "bgl", "top": 3},
            sql=["SELECT alert_category, count(*) FROM bgl_entry GROUP BY 1 ORDER BY 2 DESC LIMIT %s"],
            params=[[3]],
            total_matches=41,
            columns=COUNT_BY_COLUMNS,
            rows=[
                [
                    "bgl",
                    "KERNDTLB",
                    KERNDTLB,
                    43.83,
                    541,
                    "2005-06-03T15:42:50",
                    "2005-12-27T09:12:01",
                    0,
                    12,
                    12,
                    "median_week",
                    "unusual_high",
                ],
                [
                    "bgl",
                    "KERNSTOR",
                    KERNSTOR,
                    18.22,
                    1103,
                    "2005-06-05T02:11:09",
                    "2005-11-15T18:20:44",
                    418,
                    950,
                    950,
                    "median_week",
                    "normal",
                ],
                [
                    "bgl",
                    "APPSEV",
                    APPSEV,
                    14.25,
                    2048,
                    "2005-06-12T07:30:00",
                    "2006-01-03T03:25:12",
                    2210,
                    1640,
                    1640,
                    "median_week",
                    "normal",
                ],
                [
                    "bgl",
                    "(other)",
                    OTHER_COUNT,
                    23.7,
                    61022,
                    "2005-06-03T15:42:50",
                    "2006-01-04T08:00:05",
                    9120,
                    2871,
                    2871,
                    "median_week",
                    "normal",
                ],
            ],
            summary={
                "group_by": "alert_category",
                "total": ALERT_TOTAL,
                "groups_total": 41,
                "top_n": 3,
                "top_n_sum": TOP_N_SUM,
                "top_n_share_pct": 76.3,
                "other_count": OTHER_COUNT,
                "other_groups": 38,
            },
            notes=["Showing 3 of 41 groups; the (other) row holds the remaining 38 groups."],
        ),
        _evidence(
            "E2",
            "search_logs",
            arguments={"dataset": "bgl", "query": "data TLB error", "limit": 2},
            total_matches=KERNDTLB,
            columns=["entry_id", "ts", "node", "component", "level", "alert_category", "message"],
            rows=[
                [
                    222715,
                    "2005-06-11T17:32:07.250000",
                    "R30-M0-N9-C:J16-U01",
                    "KERNEL",
                    "FATAL",
                    "KERNDTLB",
                    "data TLB error interrupt",
                ],
                [
                    222716,
                    "2005-06-11T17:32:07.312000",
                    "R30-M0-N9-C:J16-U01",
                    "KERNEL",
                    "FATAL",
                    "KERNDTLB",
                    "data TLB error interrupt",
                ],
            ],
            summary={
                "entries_total": KERNDTLB,
                "top_components": {"KERNEL": KERNDTLB},
                "distinct_nodes": 541,
            },
        ),
        _evidence(
            "E3",
            "list_incidents",
            role="team_lead",
            arguments={"dataset": "bgl", "pattern_key": "KERNDTLB", "gap_s": 300, "merge_scope": "all"},
            columns=[
                "incident_id",
                "pattern_key",
                "t0",
                "t1",
                "entries",
                "nodes",
                "midplanes",
                "racks",
                "top_node",
                "top_node_share",
                "shape",
            ],
            rows=[
                [
                    17,
                    "KERNDTLB",
                    "2005-06-11T17:32:07",
                    "2005-06-11T23:58:40",
                    STORM_ENTRIES,
                    1,
                    1,
                    1,
                    "R30-M0-N9-C:J16-U01",
                    1.0,
                    "single-node storm",
                ],
                [
                    18,
                    "KERNDTLB",
                    "2005-08-02T04:10:00",
                    "2005-08-02T04:19:31",
                    400,
                    380,
                    44,
                    21,
                    "R12-M1-N4-C:J07-U11",
                    0.0075,
                    "multi-midplane",
                ],
                [
                    19,
                    "KERNDTLB",
                    "2005-11-15T12:00:00",
                    "2005-11-15T12:03:10",
                    152,
                    150,
                    30,
                    18,
                    "R44-M0-N2-C:J10-U01",
                    0.0132,
                    "multi-midplane",
                ],
            ],
            summary={"incidents_total": 3, "entries_total": KERNDTLB},
        ),
        _evidence(
            "E4",
            "trend",
            role="team_lead",
            arguments={"dataset": "bgl", "alert_categories": ["KERNDTLB"], "bucket": "week"},
            total_matches=4,
            columns=["bucket", "(all)"],
            rows=[
                ["2005-06-06T00:00:00", STORM_ENTRIES + 70],
                ["2005-06-13T00:00:00", 30],
                ["2005-08-01T00:00:00", 400],
                ["2005-11-14T00:00:00", 52],
            ],
            summary={
                "bucket": "week",
                "buckets": 4,
                "total": KERNDTLB,
                "series_total": KERNDTLB,
                "other_count": 0,
            },
        ),
        _evidence(
            "E5",
            "describe_corpus",
            data_scope={"datasets": ["bgl", "openstack"]},
            total_matches=2,
            columns=["dataset", "entries", "first_entry", "last_entry"],
            rows=[
                ["bgl", 4_747_963, "2005-06-03T15:42:50", "2006-01-04T08:00:05"],
                ["openstack", 207_820, "2017-05-14T19:39:01", "2017-05-17T12:02:35"],
            ],
            summary={"entries_total": 4_955_783},
        ),
        _evidence(
            "E6",
            "search_logs",
            data_scope={"datasets": ["openstack"], "time_input": "last 30d"},
            arguments={"dataset": "openstack", "query": "memory"},
            total_matches=28,
            columns=["entry_id", "ts", "component", "message"],
            rows=[
                [
                    201288,
                    "2017-05-14T21:07:37.103000",
                    "nova.compute.claims",
                    "Total memory: 64172 MB, used: 512.00 MB",
                ],
                [
                    52312,
                    "2017-05-16T06:25:02.870000",
                    "nova.osapi_compute.wsgi.server",
                    '10.11.10.1 "GET /v2/servers/detail HTTP/1.1" status: 503 len: 323 time: 0.0080531',
                ],
            ],
            summary={"entries_total": 28},
        ),
        _evidence(
            "E7",
            "get_context",
            arguments={"dataset": "bgl", "entry_id": 222715, "before": 1, "after": 1},
            total_matches=3,
            columns=["entry_id", "ts", "node", "component", "level", "message", "offset_s", "anchor"],
            rows=[
                [
                    222714,
                    "2005-06-11T17:32:05.100000",
                    "R30-M0-N9-C:J16-U01",
                    "KERNEL",
                    "INFO",
                    "instruction cache parity error corrected",
                    -2.15,
                    False,
                ],
                [
                    222715,
                    "2005-06-11T17:32:07.250000",
                    "R30-M0-N9-C:J16-U01",
                    "KERNEL",
                    "FATAL",
                    "data TLB error interrupt",
                    0.0,
                    True,
                ],
                [
                    222716,
                    "2005-06-11T17:32:07.312000",
                    "R30-M0-N9-C:J16-U01",
                    "KERNEL",
                    "FATAL",
                    "data TLB error interrupt",
                    0.062,
                    False,
                ],
            ],
            summary={"before_returned": 1, "after_returned": 1, "entries_in_scope": 3},
        ),
        _evidence(
            "E8",
            "top_patterns",
            role="manager",
            arguments={"dataset": "bgl", "sort": "priority", "top": 2},
            total_matches=41,
            columns=["pattern_key", "entries", "incidents", "midplanes", "active_weeks", "priority_rank"],
            rows=[["KERNMNTF", 26_426, 83, 64, 9, 1], ["KERNDTLB", KERNDTLB, 3, 61, 3, 10]],
            summary={"patterns_total": 41},
        ),
    ]
