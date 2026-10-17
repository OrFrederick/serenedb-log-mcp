"""`trend` end to end through MCP, on the synthetic corpus of tests/support/aggregation_corpus.py."""

from __future__ import annotations

from datetime import date

import pytest
from aggregation_corpus import (
    BGL_TOTAL,
    MMCS,
    NEWCOMP,
    OS_ERRORS,
    STEADY,
    STORM,
    T_NEW,
    T_OLD,
    T_TLB,
)
from aggregation_tools import call, call_error, independent

pytestmark = pytest.mark.db


def series(result: dict[str, object]) -> dict[str, dict[str, object]]:
    summary = result["summary"]
    assert isinstance(summary, dict)
    return {entry["series"]: entry for entry in summary["series"]}


def column(result: dict[str, object], name: str) -> list[int]:
    columns = result["columns"]
    rows = result["rows"]
    assert isinstance(columns, list)
    assert isinstance(rows, list)
    index = columns.index(name)
    return [row[index] for row in rows]


def test_an_ungrouped_trend_is_zero_filled_and_adds_up(corpus: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="day", alert_categories=["KERNDTLB"])

    assert result["columns"] == ["bucket", "(all)"]
    assert result["total_matches"] == result["returned"] == 61
    assert result["truncated"] is False
    assert result["rows"][0] == ["2005-06-01", 0]
    assert result["rows"][-1][0] == "2005-07-31"
    assert sum(column(result, "(all)")) == STORM
    assert [row for row in result["rows"] if row[1]] == [["2005-07-20", STORM]]
    entry = series(result)["(all)"]
    assert (entry["count"], entry["peak_bucket"], entry["peak_count"]) == (STORM, "2005-07-20", STORM)
    assert result["summary"]["total"] == STORM


def test_the_buckets_match_time_bucket_in_independent_sql(corpus: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="week")

    utc = "make_timestamp(CAST(round(epoch_utc * 1000000) AS BIGINT))"
    expected = independent(
        corpus, f"SELECT time_bucket(INTERVAL '7 days', {utc}), count(*) FROM {{bgl}} GROUP BY 1 ORDER BY 1"
    )
    assert [(date.fromisoformat(r[0]), r[1]) for r in result["rows"]] == [(b.date(), n) for b, n in expected]
    assert sum(column(result, "(all)")) == BGL_TOTAL


def test_a_grouped_trend_draws_the_largest_groups_and_counts_the_rest(corpus: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="week", group_by="component", top=2)

    assert result["columns"] == ["bucket", "KERNEL", "APP"]
    assert sum(column(result, "KERNEL")) == STEADY + STORM
    summary = result["summary"]
    assert summary["groups_total"] == 4
    assert summary["other_count"] == MMCS + NEWCOMP
    assert summary["total"] == BGL_TOTAL
    assert any("2 largest of 4 groups" in note for note in result["notes"])


def test_series_carry_baselines(corpus: str) -> None:
    result = call(
        "trend",
        role="team_lead",
        dataset="bgl",
        bucket="day",
        group_by="alert_category",
        time_from="2005-07-18",
        time_to="2005-07-25",
    )

    storm = series(result)["KERNDTLB"]
    assert (storm["count"], storm["prev_period"], storm["median_week"]) == (STORM, 0, 0)
    assert storm["baseline_flag"] == "unusual_high"
    steady = series(result)["(non-alert)"]
    assert (steady["count"], steady["prev_period"], steady["median_week"], steady["expected"]) == (
        14,
        14,
        14,
        14,
    )
    assert steady["baseline_flag"] == "normal"
    assert any("median_week" in note for note in result["notes"])


def test_a_pattern_with_a_drift_partner_carries_a_handoff_note(corpus: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="week", group_by="pattern")

    assert result["columns"] == ["bucket", T_TLB, T_NEW, T_OLD, "(no Pattern)"]
    entries = series(result)
    assert T_NEW in entries[T_OLD]["handoff"]
    assert T_OLD in entries[T_NEW]["handoff"]
    assert "handoff" not in entries[T_TLB]
    # One copy of each note, in its series; the notes only point there.
    assert not any("shares Pattern Family" in note for note in result["notes"])
    assert any("handoff" in note and T_OLD in note and T_NEW in note for note in result["notes"])


def test_without_pattern_ids_there_is_no_handoff_and_a_note_says_why(corpus: str) -> None:
    result = call("trend", role="team_lead", dataset="openstack", bucket="hour", group_by="pattern")

    assert result["columns"] == ["bucket", "(no Pattern)"]
    assert "handoff" not in series(result)["(no Pattern)"]
    assert any("No Pattern ids are assigned in openstack" in note for note in result["notes"])


def test_a_short_dataset_without_a_previous_period_has_no_baseline(corpus: str) -> None:
    result = call("trend", role="engineer", dataset="openstack", bucket="hour")

    assert result["total_matches"] == 49
    entry = series(result)["(all)"]
    assert entry["median_week"] is None
    assert entry["prev_period"] is None
    assert entry["baseline_flag"] == "no_baseline"


def test_a_short_dataset_falls_back_to_the_previous_period(corpus: str) -> None:
    result = call(
        "trend",
        role="engineer",
        dataset="openstack",
        bucket="hour",
        time_from="2017-05-16",
        time_to="2017-05-17",
        components=["nova.compute.manager"],
    )

    # The Data Scope ends at the Dataset's end, 2017-05-16 20:00:06, so it lasts 20 h 0 min 6 s and
    # so does the previous period, from 2017-05-15 03:59:54: hourly lines 04:00 to 23:00, 20 of them.
    window = result["summary"]["baseline"]["openstack"]
    assert (window["to"], window["previous_from"]) == ("2017-05-16T20:00:06", "2017-05-15T03:59:54")
    entry = series(result)["(all)"]
    assert (entry["count"], entry["prev_period"], entry["expected"]) == (OS_ERRORS, 20, 20)
    assert entry["baseline_flag"] == "unusual_low"
    assert entry["baseline_basis"] == "previous_period"
    assert result["total_matches"] == 21


def test_too_many_buckets_is_an_error_that_says_how_to_recover(corpus: str) -> None:
    message = call_error("trend", role="team_lead", dataset="bgl", bucket="minute")

    assert "at most 400" in message
    assert "bucket='day'" in message


def test_a_range_outside_the_dataset_is_an_error(corpus: str) -> None:
    message = call_error(
        "trend", role="team_lead", dataset="openstack", time_from="2005-06-01", time_to="2005-07-01"
    )

    assert "does not overlap" in message
    assert "2017-05-14" in message


def test_team_series_show_unmapped_components(corpus: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="week", group_by="team")

    assert sum(column(result, "(unmapped)")) == NEWCOMP
    assert any("Ownership Table" in note for note in result["notes"])


def test_the_evidence_says_counts_are_log_entries_and_ts_is_local_time(corpus: str) -> None:
    result = call("trend", role="manager", dataset="bgl", bucket="week", query="TLB error")

    assert sum(column(result, "(all)")) == STORM
    assert any("Log Entries, not Incidents" in note for note in result["notes"])
    assert any("US Pacific" in note for note in result["notes"])
    assert len(result["sql"]) == len(result["params"])


def test_a_free_standing_punctuation_word_is_refused_instead_of_a_zero_series(corpus: str) -> None:
    message = call_error("trend", role="engineer", dataset="bgl", bucket="week", query="TLB error ... ;")

    assert "has words without a letter or digit ('...', ';')" in message
    assert "Remove them" in message


def test_a_grouped_trend_without_matches_is_one_zero_series_and_zero_groups(corpus: str) -> None:
    result = call(
        "trend", role="engineer", dataset="bgl", bucket="week", group_by="component", query="zebra unicorn"
    )

    assert result["columns"] == ["bucket", "(all)"]
    assert sum(column(result, "(all)")) == 0
    assert (result["summary"]["total"], result["summary"]["groups_total"]) == (0, 0)
    assert any("No Log Entries matched" in note for note in result["notes"])


def test_an_empty_pattern_trend_skips_the_pattern_family_statement(corpus: str) -> None:
    result = call(
        "trend", role="engineer", dataset="bgl", bucket="week", group_by="pattern", query="zebra unicorn"
    )

    # The ranking, the buckets and the baselines; no family lookup over the whole Dataset.
    assert len(result["sql"]) == 3
    assert not any("family_id" in statement for statement in result["sql"])
