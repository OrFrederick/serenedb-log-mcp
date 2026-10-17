"""`list_incidents` end to end through MCP, on the lab corpus of tests/support/incident_corpus.py.

Every Incident here is known by construction; totals are also checked against independent SQL on
the same tables, so the tool cannot pass by agreeing with itself.
"""

from __future__ import annotations

from typing import Any

import incident_corpus
import pytest
from aggregation_tools import call, call_error, independent, row_dicts
from incident_corpus import (
    KERNDTLB_ENTRIES,
    NODE_A,
    NODE_B,
    NODE_C,
    OS_CHAINED_ENTRIES,
    OS_CHAINED_INCIDENTS,
    OS_ENTRIES,
    OS_INCIDENTS,
    OS_LIFECYCLE,
    STORM_ENTRIES,
    T_API,
    T_FAIL,
    T_OLD,
    T_OLD_ENTRIES,
    U1,
    U2,
    WAVE_ENTRIES,
)

from serenedb_log_mcp import toolkit
from serenedb_log_mcp.corpus.datasets import LIFECYCLE_KEY

pytestmark = pytest.mark.db


def _key(rows: list[dict[str, Any]], *columns: str) -> list[tuple[Any, ...]]:
    return [tuple(row[c] for c in columns) for row in rows]


# ----------------------------------------------------------------- the precomputed table


def test_the_golden_shape_holds_on_the_lab_storm(lab: str) -> None:
    """The ticket's golden call, on the lab: the largest KERNDTLB Incident is one node shouting,
    and the summary holds every Log Entry of the category next to the Incident count."""
    result = call("list_incidents", role="team_lead", dataset="bgl", pattern_key="KERNDTLB", sort="entries")

    rows = row_dicts(result)
    assert _key(rows, "entries", "nodes", "top_node", "shape") == [
        (STORM_ENTRIES, 1, NODE_B, "single-node storm"),
        (WAVE_ENTRIES, 2, NODE_A, "multi-midplane"),
    ]
    summary = result["summary"]
    assert summary["entries_total"] == KERNDTLB_ENTRIES
    assert summary["incidents_total"] == 2
    assert summary["computed"] == "precomputed table"
    assert result["total_matches"] == 2
    assert result["truncated"] is False


def test_the_summary_carries_the_spread_totals_of_the_log_entries(lab: str) -> None:
    result = call("list_incidents", role="manager", dataset="bgl", pattern_key="KERNDTLB")

    summary = result["summary"]
    assert (summary["nodes_total"], summary["nodecards_total"], summary["midplanes_total"]) == (3, 3, 3)
    assert summary["racks_total"] == 2
    assert summary["incidents_by_shape"] == {"single-node storm": 1, "multi-midplane": 1}


def test_a_wave_on_two_nodes_has_one_burst_per_node(lab: str) -> None:
    result = call("list_incidents", role="engineer", dataset="bgl", pattern_key="KERNDTLB", sort="time")

    wave = row_dicts(result)[0]
    assert (wave["entries"], wave["bursts"], wave["nodes"], wave["midplanes"], wave["racks"]) == (
        3,
        2,
        2,
        2,
        2,
    )
    assert wave["top_node_share"] == pytest.approx(2 / 3, abs=1e-4)
    assert wave["duration_s"] == 100


def test_a_template_id_is_a_pattern_key_too(lab: str) -> None:
    result = call("list_incidents", role="team_lead", dataset="bgl", pattern_key=T_OLD, limit=5)

    assert result["summary"]["incidents_total"] == T_OLD_ENTRIES
    assert result["summary"]["entries_total"] == T_OLD_ENTRIES
    assert result["returned"] == 5
    assert result["truncated"] is True


def test_the_whole_bgl_table_matches_independent_sql(lab: str) -> None:
    result = call("list_incidents", role="team_lead", dataset="bgl")

    ((incidents, entries),) = independent(
        lab,
        "SELECT count(*) FILTER (WHERE previous IS NULL OR epoch_utc - previous > 300), count(*) FROM ("
        "SELECT epoch_utc, lag(epoch_utc) OVER (PARTITION BY pattern_key ORDER BY epoch_utc, entry_id) "
        "AS previous FROM {bgl} WHERE pattern_key IS NOT NULL) chained",
    )
    assert result["summary"]["incidents_total"] == incidents
    assert result["summary"]["entries_total"] == entries
    assert result["total_matches"] == incidents


# ----------------------------------------------------------------- query-time chaining


def test_a_shorter_gap_chains_the_filtered_set_at_query_time(lab: str) -> None:
    # The storm's Log Entries are 30 s apart and the wave's 50 s, so at 20 s every one is alone.
    result = call("list_incidents", role="team_lead", dataset="bgl", pattern_key="KERNDTLB", gap_s=20)

    summary = result["summary"]
    assert summary["computed"] == "chained at query time"
    assert summary["incidents_total"] == KERNDTLB_ENTRIES
    assert summary["entries_total"] == KERNDTLB_ENTRIES
    assert summary["gap_s"] == 20


def test_merging_per_source_splits_the_wave_by_node(lab: str) -> None:
    result = call(
        "list_incidents",
        role="team_lead",
        dataset="bgl",
        pattern_key="KERNDTLB",
        merge_scope="source",
        sort="time",
    )

    rows = row_dicts(result)
    assert result["summary"]["incidents_total"] == 3
    assert _key(rows, "top_node", "entries", "nodes") == [(NODE_A, 2, 1), (NODE_C, 1, 1), (NODE_B, 100, 1)]
    assert [row["incident_id"] for row in rows] == [1, 2, 3]


def test_query_time_chaining_at_the_defaults_gives_the_table(lab: str) -> None:
    """The point of precomputing (#1): the table is the query-time answer, not an approximation."""
    table = call("list_incidents", role="engineer", dataset="bgl", pattern_key="KERNDTLB", sort="time")
    chained = call(
        "list_incidents",
        role="engineer",
        dataset="bgl",
        pattern_key="KERNDTLB",
        sort="time",
        time_from="2005-01-01",
    )

    assert chained["summary"]["computed"] == "chained at query time"
    assert chained["rows"] == table["rows"]
    for name in ("entries_total", "incidents_total", "nodes_total", "midplanes_total", "racks_total"):
        assert chained["summary"][name] == table["summary"][name], name


def test_a_time_range_chains_only_the_log_entries_inside_it(lab: str) -> None:
    result = call(
        "list_incidents", role="team_lead", dataset="bgl", pattern_key="KERNDTLB",
        time_from="2005-07-20T17:00:00", time_to="2005-07-20T17:10:00",
    )  # fmt: skip

    # 10 minutes of a storm with one Log Entry every 30 s, logged from 10:00 US Pacific (PDT), so
    # 17:00 on the UTC clock that time inputs use since #74.
    assert result["summary"]["entries_total"] == 20
    assert result["summary"]["incidents_total"] == 1
    assert result["data_scope"]["time_from"] == "2005-07-20T17:00:00"


def test_alert_categories_narrow_a_non_default_call(lab: str) -> None:
    result = call("list_incidents", role="team_lead", dataset="bgl", alert_categories=["KERNDTLB"], gap_s=60)

    assert result["summary"]["entries_total"] == KERNDTLB_ENTRIES
    assert result["summary"]["incidents_total"] == 2


def test_a_non_default_call_without_a_narrowing_filter_is_refused(lab: str) -> None:
    text = call_error("list_incidents", role="team_lead", dataset="bgl", gap_s=60)

    assert "pattern_key" in text
    assert "time_from" in text


# ----------------------------------------------------------------- sorting and limits


@pytest.mark.parametrize(
    ("sort", "first"),
    [("entries", STORM_ENTRIES), ("time", WAVE_ENTRIES), ("recent", STORM_ENTRIES), ("spread", WAVE_ENTRIES),
     ("duration", STORM_ENTRIES)],
)  # fmt: skip
def test_every_sort_puts_the_expected_incident_first(lab: str, sort: str, first: int) -> None:
    result = call("list_incidents", role="team_lead", dataset="bgl", pattern_key="KERNDTLB", sort=sort)

    assert row_dicts(result)[0]["entries"] == first


def test_an_unknown_alert_category_suggests_the_close_one(lab: str) -> None:
    text = call_error("list_incidents", role="team_lead", dataset="bgl", pattern_key="KERNDTLBB")

    assert "KERNDTLB" in text


def test_a_pattern_key_without_incidents_says_so(lab: str) -> None:
    result = call("list_incidents", role="team_lead", dataset="bgl", pattern_key="ffffffffffffffff")

    assert result["summary"]["incidents_total"] == 0
    assert result["rows"] == []
    assert any("list_patterns" in note for note in result["notes"])


# ----------------------------------------------------------------- OpenStack Request Failures


def test_openstack_request_failures_come_from_the_precomputed_table(lab: str) -> None:
    result = call("list_incidents", role="team_lead", dataset="openstack", sort="time", limit=100)

    summary = result["summary"]
    assert summary["computed"] == "precomputed table"
    assert summary["incidents_total"] == OS_INCIDENTS
    assert summary["entries_total"] == OS_ENTRIES
    # Sources are instances, or the host for a Log Entry that names no instance.
    assert (summary["sources_total"], summary["instances_total"], summary["hosts_total"]) == (5, 4, 1)
    assert "shape" not in result["columns"]
    assert {"sources", "top_source", "top_source_share"} <= set(result["columns"])


def test_a_503_response_is_a_request_failure_and_a_200_of_its_pattern_is_not(lab: str) -> None:
    result = call("list_incidents", role="engineer", dataset="openstack", pattern_key=T_API)

    assert _key(row_dicts(result), "entries", "sources", "top_source") == [(2, 1, "nova-api")]
    assert result["summary"]["entries_total"] == 2


def test_error_lines_chain_per_pattern_with_instances_as_sources(lab: str) -> None:
    result = call("list_incidents", role="engineer", dataset="openstack", pattern_key=T_FAIL, sort="time")

    assert _key(row_dicts(result), "incident_id", "entries", "top_source") == [(1, 2, U1), (2, 1, U2)]


def test_instances_whose_lifecycle_never_completes_are_request_failures(lab: str) -> None:
    result = call(
        "list_incidents", role="engineer", dataset="openstack", pattern_key=LIFECYCLE_KEY, sort="time"
    )

    assert {row["top_source"]: row["entries"] for row in row_dicts(result)} == OS_LIFECYCLE
    assert result["summary"]["instances_total"] == len(OS_LIFECYCLE)


def test_openstack_query_time_chaining_covers_the_chained_request_failures(lab: str) -> None:
    table = call("list_incidents", role="engineer", dataset="openstack", sort="time")
    chained = call(
        "list_incidents", role="engineer", dataset="openstack", sort="time", time_from="2017-05-01"
    )

    assert chained["summary"]["incidents_total"] == OS_CHAINED_INCIDENTS
    assert chained["summary"]["entries_total"] == OS_CHAINED_ENTRIES
    expected = [row for row in row_dicts(table) if row["pattern_key"] != LIFECYCLE_KEY]
    assert row_dicts(chained) == expected


def test_openstack_rejects_an_alert_category_as_pattern_key(lab: str) -> None:
    text = call_error("list_incidents", role="engineer", dataset="openstack", pattern_key="KERNDTLB")

    assert "template" in text


def test_the_manager_receives_the_incident_rows(lab: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Incident rows hold counts and source names, never Log Entry text, so the Role shaping leaves
    # them to the Manager.
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.DEFAULT_ROLE_HOOK)

    result = call("list_incidents", role="manager", dataset="bgl", pattern_key="KERNDTLB")

    assert "message" not in result["columns"]
    assert result["returned"] == 2
    assert {row["top_node"] for row in row_dicts(result)} == {NODE_A, NODE_B}


# ----------------------------------------------------------------- the derive step


def test_running_the_openstack_step_again_gives_the_same_table(lab: str, database_url: str) -> None:
    query = "SELECT * FROM {inc} ORDER BY dataset, pattern_key, incident_id"
    before = incident_corpus.incident_rows(database_url, lab, query)

    incident_corpus.run_step(database_url, lab, "35_os_incidents.sql")

    assert incident_corpus.incident_rows(database_url, lab, query) == before
    assert len([row for row in before if row[0] == "openstack"]) == OS_INCIDENTS
