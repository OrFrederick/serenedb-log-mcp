"""`top_patterns` against the synthetic corpus (T28): ranks, Incidents, Pareto share, flags, hand-offs
and the priority band, each with a value known by design (tests/support/top_patterns_corpus.py)."""

from __future__ import annotations

import pytest
from aggregation_tools import call, call_error, row_dicts
from top_patterns_corpus import (
    BGL_PATTERNS,
    ENTRIES,
    INCIDENTS,
    OS_ENTRIES,
    OS_INCIDENTS,
    T_NEW,
    T_OLD,
    T_OS_FAIL,
)

from serenedb_log_mcp.tools.top_patterns import CANDIDATE_FILTER


def bgl(**arguments: object) -> list[dict[str, object]]:
    return row_dicts(call("top_patterns", role="team_lead", dataset="bgl", **arguments))


def test_sort_entries_ranks_patterns_by_log_entries_with_incidents_beside_them() -> None:
    result = call("top_patterns", role="team_lead", dataset="bgl")
    rows = row_dicts(result)

    assert [r["pattern"] for r in rows] == list(BGL_PATTERNS)
    assert {r["pattern"]: r["entries"] for r in rows} == ENTRIES
    assert {r["pattern"]: r["incidents"] for r in rows} == INCIDENTS
    assert result["total_matches"] == 4
    summary = result["summary"]["datasets"]["bgl"]
    assert summary["entries_total"] == sum(ENTRIES.values())
    assert summary["incidents_total"] == sum(INCIDENTS.values())


def test_pareto_share_is_cumulative_over_log_entries() -> None:
    rows = bgl()
    total = sum(ENTRIES.values())

    assert rows[0]["share_pct"] == round(100 * 100 / total, 2)
    assert rows[1]["pareto_pct"] == round(100 * 162 / total, 2)
    assert rows[-1]["pareto_pct"] == 100.0


def test_kind_and_template_text_tell_alert_categories_from_templates() -> None:
    rows = {r["pattern"]: r for r in bgl()}

    assert rows["KERNDTLB"]["kind"] == "alert_category"
    assert rows["KERNDTLB"]["template_text"] is None
    assert rows[T_NEW]["kind"] == "template"
    assert rows[T_NEW]["template_text"] == "steady kernel message"


@pytest.mark.parametrize(
    ("sort", "first"),
    [("incidents", T_NEW), ("sources", "KERNDTLB"), ("recent", T_NEW)],
)
def test_other_sorts(sort: str, first: str) -> None:
    assert bgl(sort=sort)[0]["pattern"] == first


def test_the_wording_drift_hands_off_within_the_family() -> None:
    rows = {r["pattern"]: r for r in bgl()}

    assert rows[T_OLD]["handoff_to"] == T_NEW
    assert rows[T_OLD]["handoff_from"] is None
    assert rows[T_NEW]["handoff_from"] == T_OLD
    assert rows[T_NEW]["handoff_to"] is None
    assert rows["KERNDTLB"]["handoff_to"] is None


def test_chronic_marks_the_weekly_pattern_and_not_the_storm() -> None:
    rows = {r["pattern"]: r for r in bgl()}

    assert rows["APPSEV"]["chronic"] is True
    assert rows["APPSEV"]["active_weeks"] == 9
    assert rows["KERNDTLB"]["chronic"] is False


def test_new_pattern_is_relative_to_the_end_of_the_data_scope() -> None:
    whole = {r["pattern"]: r for r in bgl()}
    before_august = {r["pattern"]: r for r in bgl(time_to="2005-07-21")}

    assert whole["KERNDTLB"]["new"] is False
    assert before_august["KERNDTLB"]["new"] is True
    assert before_august[T_NEW]["new"] is False


def test_priority_ranks_only_alert_categories_and_shows_the_band() -> None:
    result = call("top_patterns", role="manager", dataset="bgl", sort="priority")
    rows = row_dicts(result)

    assert [r["pattern"] for r in rows] == ["APPSEV", "KERNDTLB"]
    assert [r["band"] for r in rows] == [True, True]
    assert rows[0]["priority"] == pytest.approx((1 + 0.75 + 1 + 0.5 + 1) / 5)
    assert rows[1]["priority"] == pytest.approx((0.5 + 0.75 + 0.5 + 1 + 0.5) / 5)
    assert rows[1]["days_since_last"] == 11
    assert result["data_scope"]["filters"]["candidates"] == CANDIDATE_FILTER
    assert any(CANDIDATE_FILTER in note for note in result["notes"])


def test_openstack_priority_candidates_are_request_failures() -> None:
    result = call("top_patterns", role="manager", dataset="openstack", sort="priority")
    rows = row_dicts(result)

    assert [r["pattern"] for r in rows] == [T_OS_FAIL]
    assert rows[0]["incidents"] == 1
    assert rows[0]["spread"] == 1
    assert any("shorter than 28 days" in note for note in result["notes"])


def test_both_datasets_rank_each_dataset_on_its_own() -> None:
    result = call("top_patterns", role="team_lead", top=2)
    rows = row_dicts(result)

    assert [(r["dataset"], r["pattern"]) for r in rows][:2] == [("bgl", "KERNDTLB"), ("bgl", T_NEW)]
    assert {r["pattern"]: r["incidents"] for r in rows if r["dataset"] == "openstack"} == {
        k: OS_INCIDENTS[k] for k in list(OS_ENTRIES)[:2]
    }
    assert result["total_matches"] == 7
    assert result["truncated"] is True


def test_filters_narrow_the_patterns() -> None:
    rows = bgl(levels=["FATAL"])

    assert {r["pattern"] for r in rows} == {"KERNDTLB", "APPSEV"}


def test_an_unknown_component_is_an_actionable_error() -> None:
    assert "components" in call_error("top_patterns", role="engineer", dataset="bgl", components=["NOPE"])
