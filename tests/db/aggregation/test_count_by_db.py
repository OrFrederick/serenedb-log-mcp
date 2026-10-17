"""`count_by` end to end through MCP, on the synthetic corpus of tests/support/aggregation_corpus.py.

Every number here is known by construction; the group counts are also checked against independent
SQL on the same tables, so the tool cannot pass by agreeing with itself.
"""

from __future__ import annotations

import pytest
from aggregation_corpus import (
    BGL_TOTAL,
    F_DRIFT,
    NEWCOMP,
    NODE_A,
    NODE_B,
    OS_API,
    OS_TOTAL,
    STEADY,
    STORM,
    T_NEW,
    T_OLD,
    T_TLB,
)
from aggregation_tools import call, call_error, independent, row_dicts

pytestmark = pytest.mark.db


def test_group_by_dataset_covers_the_whole_corpus_with_exact_totals(corpus: str) -> None:
    result = call("count_by", role="manager", group_by="dataset")

    rows = row_dicts(result)
    assert [(r["dataset"], r["count"]) for r in rows] == [("bgl", BGL_TOTAL), ("openstack", OS_TOTAL)]
    assert result["summary"]["total"] == BGL_TOTAL + OS_TOTAL
    assert result["summary"]["groups_total"] == 2
    assert result["summary"]["top_n_sum"] == BGL_TOTAL + OS_TOTAL
    assert result["summary"]["other_count"] == 0
    assert result["total_matches"] == 2
    assert result["truncated"] is False
    assert result["data_scope"]["datasets"] == ["bgl", "openstack"]
    assert result["columns"][:2] == ["dataset", "count"]


def test_component_counts_match_independent_sql(corpus: str) -> None:
    result = call("count_by", role="team_lead", group_by="component")

    expected = independent(
        corpus,
        "SELECT 'bgl', component, count(*) FROM {bgl} GROUP BY 2 "
        "UNION ALL SELECT 'openstack', component, count(*) FROM {os} GROUP BY 2",
    )
    assert {(r["dataset"], r["component"]): r["count"] for r in row_dicts(result)} == {
        (d, c): n for d, c, n in expected
    }
    counts = [r["count"] for r in row_dicts(result)]
    assert counts == sorted(counts, reverse=True)


def test_each_group_has_share_sources_and_first_and_last_seen(corpus: str) -> None:
    rows = {r["component"]: r for r in row_dicts(call("count_by", role="team_lead", group_by="component"))}

    kernel = rows["KERNEL"]
    assert kernel["count"] == STEADY + STORM
    assert kernel["share_pct"] == round(100 * (STEADY + STORM) / (BGL_TOTAL + OS_TOTAL), 2)
    assert kernel["distinct_sources"] == 2  # NODE_A and NODE_B
    assert kernel["first_seen"] == "2005-06-01T10:00:00"
    assert kernel["last_seen"] == "2005-07-31T14:00:00"
    # OpenStack sources are instances; the API lines carry none.
    assert rows["nova.osapi_compute.wsgi.server"]["distinct_sources"] == 0
    assert rows["nova.compute.manager"]["distinct_sources"] == 2


def test_team_joins_the_ownership_table_and_shows_unmapped_components(corpus: str) -> None:
    result = call("count_by", role="team_lead", group_by="team")

    teams = {(r["dataset"], r["team"]): r["count"] for r in row_dicts(result)}
    assert teams[("bgl", "Kernel Team")] == STEADY + STORM
    assert teams[("bgl", "(unmapped)")] == NEWCOMP
    assert teams[("openstack", "API Team")] == OS_API
    assert any("Ownership Table" in note for note in result["notes"])


def test_the_other_row_keeps_the_totals_exact_and_a_single_hidden_group_is_shown(corpus: str) -> None:
    result = call("count_by", role="team_lead", group_by="component", top=2)

    rows = row_dicts(result)
    shown = [(r["dataset"], r["component"], r["count"]) for r in rows]
    # Top 2 overall, then the one hidden OpenStack group as itself, then BGL's 3 hidden groups.
    assert shown == [
        ("bgl", "KERNEL", STEADY + STORM),
        ("openstack", "nova.compute.manager", OS_TOTAL - OS_API),
        ("openstack", "nova.osapi_compute.wsgi.server", OS_API),
        ("bgl", "(other)", BGL_TOTAL - STEADY - STORM),
    ]
    summary = result["summary"]
    assert summary["groups_total"] == 6
    assert summary["top_n_sum"] + summary["other_count"] == summary["total"] == BGL_TOTAL + OS_TOTAL
    assert summary["other_count"] == BGL_TOTAL - STEADY - STORM
    assert summary["other_groups"] == 3
    assert result["truncated"] is True
    assert result["returned"] == 4
    assert any("(other)" in note and "exact" in note for note in result["notes"])
    other = rows[-1]
    assert other["distinct_sources"] == 3  # NODE_C, 'NULL' and NODE_A (NEWCOMP)


def test_baselines_flag_the_storm_week_and_leave_the_steady_lines_normal(corpus: str) -> None:
    result = call(
        "count_by",
        role="team_lead",
        group_by="alert_category",
        dataset="bgl",
        time_from="2005-07-18",
        time_to="2005-07-25",
    )

    rows = {r["alert_category"]: r for r in row_dicts(result)}
    storm = rows["KERNDTLB"]
    assert (storm["count"], storm["prev_period"], storm["median_week"]) == (STORM, 0, 0)
    assert storm["expected"] == 0
    assert storm["baseline_flag"] == "unusual_high"
    assert storm["baseline_basis"] == "median_week"
    steady = rows["(non-alert)"]
    assert (steady["count"], steady["prev_period"], steady["median_week"]) == (14, 14, 14)
    assert steady["baseline_flag"] == "normal"
    assert rows["APPSEV"]["median_week"] == 1
    assert rows["APPSEV"]["baseline_flag"] == "normal"
    # The median week comes from the 6 whole weeks outside the Data Scope's week.
    assert any("median_week" in note and "outside the Data Scope (bgl 6)" in note for note in result["notes"])


def test_a_named_baseline_period_replaces_the_previous_period(corpus: str) -> None:
    result = call(
        "count_by",
        role="team_lead",
        group_by="alert_category",
        dataset="bgl",
        time_from="2005-07-18",
        time_to="2005-07-25",
        baseline_from="2005-06-06",
        baseline_to="2005-07-04",
    )

    rows = {r["alert_category"]: r for r in row_dicts(result)}
    assert rows["(non-alert)"]["prev_period"] == 4 * 14
    assert rows["APPSEV"]["prev_period"] == 4
    assert result["summary"]["baseline"]["bgl"]["previous_from"] == "2005-06-06T00:00:00"
    # The named 4-week period drives expected, scaled to the 1-week Data Scope.
    assert {r["baseline_basis"] for r in rows.values()} == {"named_period"}
    assert (rows["(non-alert)"]["expected"], rows["(non-alert)"]["baseline_flag"]) == (14, "normal")
    assert rows["APPSEV"]["expected"] == 1
    assert rows["KERNDTLB"]["baseline_flag"] == "unusual_high"


def test_without_a_time_range_there_is_no_previous_period(corpus: str) -> None:
    rows = row_dicts(call("count_by", role="manager", group_by="dataset"))

    bgl, openstack = rows
    assert bgl["prev_period"] is None
    # OpenStack spans under three days: no whole week, no previous period, so no baseline.
    assert openstack["median_week"] is None
    assert openstack["baseline_flag"] == "no_baseline"


def test_a_whole_dataset_scope_flags_nothing_as_unusual(corpus: str) -> None:
    # The scope covers every week, so no week is left to say what a normal week looks like.
    result = call("count_by", role="manager", group_by="alert_category", dataset="bgl")

    rows = row_dicts(result)
    assert {r["baseline_flag"] for r in rows} == {"no_baseline"}
    assert {r["baseline_basis"] for r in rows} == {"none"}
    assert {r["median_week"] for r in rows} == {None}
    assert any("outside the Data Scope" in note and "trend" in note for note in result["notes"])


def test_a_full_text_query_counts_only_matching_log_entries(corpus: str) -> None:
    result = call("count_by", role="engineer", group_by="component", query="TLB error")

    assert [(r["component"], r["count"]) for r in row_dicts(result)] == [("KERNEL", STORM)]
    assert result["data_scope"]["filters"]["query"] == "TLB error"


def test_group_by_pattern_uses_the_pattern_ids(corpus: str) -> None:
    rows = row_dicts(call("count_by", role="team_lead", group_by="pattern", dataset="bgl"))

    assert {r["pattern"]: r["count"] for r in rows} == {
        T_TLB: STORM,
        T_NEW: 62,
        T_OLD: 60,
        "(no Pattern)": BGL_TOTAL - STEADY - STORM,
    }


def test_pattern_ids_and_family_id_filter_the_count(corpus: str) -> None:
    by_ids = call("count_by", role="team_lead", group_by="dataset", pattern_ids=[T_OLD, T_NEW.upper()])
    by_family = call("count_by", role="team_lead", group_by="dataset", family_id=F_DRIFT)

    assert [(r["dataset"], r["count"]) for r in row_dicts(by_ids)] == [("bgl", STEADY)]
    assert [(r["dataset"], r["count"]) for r in row_dicts(by_family)] == [("bgl", STEADY)]
    assert by_ids["data_scope"]["filters"]["pattern_ids"] == [T_OLD, T_NEW]
    assert by_family["data_scope"]["filters"]["family_id"] == F_DRIFT


def test_component_level_and_source_filters_combine(corpus: str) -> None:
    result = call(
        "count_by",
        role="engineer",
        group_by="level",
        components=["kernel"],
        levels=["FATAL"],
        sources=[NODE_B, NODE_A],
    )

    assert [(r["level"], r["count"]) for r in row_dicts(result)] == [("FATAL", STORM)]
    assert result["data_scope"]["filters"]["components"] == ["KERNEL"]


def test_a_bgl_only_dimension_narrows_the_scope_to_bgl_with_a_note(corpus: str) -> None:
    result = call("count_by", role="team_lead", group_by="rack")

    assert result["data_scope"]["datasets"] == ["bgl"]
    racks = {r["rack"]: r["count"] for r in row_dicts(result)}
    assert racks["(no rack)"] == 3
    assert sum(racks.values()) == BGL_TOTAL
    assert any("only in BGL" in note for note in result["notes"])


def test_a_bgl_only_dimension_on_openstack_is_an_error_that_says_what_to_do(corpus: str) -> None:
    message = call_error("count_by", role="team_lead", group_by="midplane", dataset="openstack")

    assert "BGL" in message
    assert "dataset='bgl'" in message


def test_a_misspelt_component_gets_a_suggestion(corpus: str) -> None:
    message = call_error("count_by", role="team_lead", group_by="level", components=["KERNAL"])

    assert "Did you mean 'KERNEL'" in message


def test_no_match_is_a_result_with_a_hint_not_an_error(corpus: str) -> None:
    result = call("count_by", role="engineer", group_by="component", query="zebra unicorn")

    assert result["rows"] == []
    assert result["summary"]["total"] == 0
    assert result["total_matches"] == 0
    assert any("No Log Entries matched" in note for note in result["notes"])
    assert not any("median_week" in note for note in result["notes"])


def test_the_evidence_holds_the_statements_and_says_counts_are_log_entries(corpus: str) -> None:
    result = call("count_by", role="team_lead", group_by="component", top=2)

    assert len(result["sql"]) == len(result["params"]) == 2
    assert all("%s" in statement for statement in result["sql"])
    assert any("Log Entries, not Incidents" in note for note in result["notes"])
    assert any("never round" in note for note in result["notes"])
    assert result["arguments"]["group_by"] == "component"
    assert result["summary"]["group_by"] == "component"
    # The Data Scope records the filters applied; group_by and top are call parameters.
    assert "group_by" not in result["data_scope"]["filters"]
    assert "top" not in result["data_scope"]["filters"]


def test_relative_time_resolves_against_the_dataset_end(corpus: str) -> None:
    result = call("count_by", role="team_lead", group_by="dataset", dataset="bgl", time_from="last 7d")

    scope = result["data_scope"]
    assert scope["time_input"] == "last 7d"
    assert scope["time_to"] == "2005-07-31T14:00:01"
    expected = independent(corpus, "SELECT count(*) FROM {bgl} WHERE ts >= TIMESTAMP '2005-07-24 14:00:01'")[
        0
    ][0]
    assert row_dicts(result)[0]["count"] == expected
    assert row_dicts(result)[0]["prev_period"] is not None


def test_counts_reach_the_ledger_and_can_be_fetched_again(corpus: str) -> None:
    first = call("count_by", role="team_lead", group_by="component")
    again = call("get_evidence", role="manager", evidence_id=first["evidence_id"])

    assert again["rows"] == first["rows"]
    assert again["summary"] == first["summary"]
