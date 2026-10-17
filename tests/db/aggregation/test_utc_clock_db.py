"""`trend`, `count_by` and `search_logs` on one clock, UTC, across BGL's daylight-saving fall-back
(#65, #73).

The corpus here is the fall-back alone, in its own throwaway schema with the real inverted indexes:
one Log Entry every 10 minutes from 2005-10-30 06:00 to 09:50 UTC in both Datasets. For BGL that is
23:00 PDT on 2005-10-29 to 01:50 PST, so its ts runs through the hour from 01:00 twice: 12 Log
Entries carry a local ts in 01:00-01:59, while every UTC hour holds 6. OpenStack logs the same
instants in UTC.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aggregation_corpus
import pytest
from aggregation_tools import call, independent, row_dicts

from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts

pytestmark = pytest.mark.db

PACIFIC = ZoneInfo("America/Los_Angeles")
FIRST = datetime(2005, 10, 30, 6, 0)
"""The first Log Entry in UTC; the last is 23 steps of 10 minutes later, at 09:50."""
STEPS = 24
PER_HOUR = 6


def instants() -> list[datetime]:
    return [FIRST + timedelta(minutes=10 * i) for i in range(STEPS)]


def bgl_rows() -> list[dict[str, Any]]:
    rows = []
    for at in instants():
        moment = at.replace(tzinfo=UTC)
        row = aggregation_corpus._bgl(
            moment.astimezone(PACIFIC).replace(tzinfo=None),
            aggregation_corpus.NODE_A,
            "KERNEL",
            "INFO",
            None,
            "clock message",
            None,
            None,
        )
        row["epoch_utc"] = moment.timestamp()
        rows.append(row)
    return rows


def os_rows() -> list[dict[str, Any]]:
    rows = []
    for at in instants():
        row = aggregation_corpus._os(at, "nova.compute.manager", "INFO", None, "clock message")
        row["epoch_utc"] = at.replace(tzinfo=UTC).timestamp()
        rows.append(row)
    return rows


def numbered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for entry_id, row in enumerate(rows, start=1):
        row["entry_id"] = entry_id
    return rows


@pytest.fixture(scope="session")
def fallback_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_65_{uuid.uuid4().hex[:8]}"
    try:
        # With the derive step's inverted indexes: search_logs reads only the index (#73).
        aggregation_corpus.build(
            database_url, schema, bgl=numbered(bgl_rows()), openstack=numbered(os_rows())
        )
        yield schema
    finally:
        aggregation_corpus.drop(database_url, schema)


@pytest.fixture
def fallback(fallback_schema: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=fallback_schema))
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    return fallback_schema


def test_the_fixture_repeats_the_local_hour(fallback: str) -> None:
    # The bug #65 fixes: on local ts, one hour bucket holds two hours of Log Entries.
    local = independent(
        fallback,
        "SELECT time_bucket(INTERVAL '1 hour', ts), count(*) FROM {bgl} GROUP BY 1 ORDER BY 1",
    )
    assert [n for _, n in local] == [PER_HOUR, PER_HOUR, 2 * PER_HOUR]


def test_an_hourly_trend_across_the_fall_back_holds_one_hour_per_bucket(fallback: str) -> None:
    result = call("trend", role="team_lead", dataset="bgl", bucket="hour")

    assert result["rows"] == [
        ["2005-10-30T06:00", PER_HOUR],
        ["2005-10-30T07:00", PER_HOUR],
        ["2005-10-30T08:00", PER_HOUR],
        ["2005-10-30T09:00", PER_HOUR],
    ]
    scope = result["data_scope"]
    assert scope["time_zone"] == "UTC"
    assert scope["time_columns"] == {"bgl": "epoch_utc"}
    assert any("epoch_utc" in note and "ts + 7 h" in note for note in result["notes"])


def test_a_time_range_is_utc_and_selects_one_of_the_two_local_hours(fallback: str) -> None:
    result = call(
        "trend",
        role="engineer",
        dataset="bgl",
        bucket="minute",
        time_from="2005-10-30T09:00",
        time_to="2005-10-30T10:00",
    )

    assert sum(row[1] for row in result["rows"]) == PER_HOUR
    assert result["rows"][0][0] == "2005-10-30T09:00"
    assert result["data_scope"]["time_from"] == "2005-10-30T09:00:00"


def test_a_logged_time_with_its_offset_picks_the_first_or_the_second_hour(fallback: str) -> None:
    first = call(
        "count_by",
        role="engineer",
        group_by="dataset",
        dataset="bgl",
        time_from="2005-10-30T01:00:00-07:00",
        time_to="2005-10-30T01:00:00-08:00",
    )

    assert row_dicts(first)[0]["count"] == PER_HOUR
    assert (first["data_scope"]["time_from"], first["data_scope"]["time_to"]) == (
        "2005-10-30T08:00:00",
        "2005-10-30T09:00:00",
    )
    assert any("converted to UTC" in note for note in first["notes"])


def test_both_datasets_share_one_clock_in_one_call(fallback: str) -> None:
    result = call(
        "count_by",
        role="manager",
        group_by="dataset",
        time_from="2005-10-30T08:00",
        time_to="2005-10-30T09:00",
    )

    assert [(r["dataset"], r["count"]) for r in row_dicts(result)] == [
        ("bgl", PER_HOUR),
        ("openstack", PER_HOUR),
    ]
    assert result["data_scope"]["time_columns"] == {"bgl": "epoch_utc", "openstack": "ts"}


def test_the_baseline_runs_on_the_same_clock_as_the_count(fallback: str) -> None:
    result = call(
        "count_by",
        role="team_lead",
        group_by="component",
        dataset="bgl",
        time_from="2005-10-30T08:00",
        time_to="2005-10-30T09:00",
    )

    (row,) = row_dicts(result)
    assert (row["count"], row["prev_period"], row["expected"], row["baseline_flag"]) == (
        PER_HOUR,
        PER_HOUR,
        PER_HOUR,
        "normal",
    )
    assert (row["first_seen"], row["last_seen"]) == ("2005-10-30T08:00:00", "2005-10-30T08:50:00")
    window = result["summary"]["baseline"]["bgl"]
    assert (window["previous_from"], window["previous_to"]) == ("2005-10-30T07:00:00", "2005-10-30T08:00:00")


def test_the_dataset_range_holds_the_last_log_entry_after_the_repeated_hour(fallback: str) -> None:
    # The last Log Entry is 01:50 PST; read as 01:50 PDT, the range would end an hour early.
    result = call("count_by", role="team_lead", group_by="dataset", dataset="bgl", time_from="last 1h")

    assert result["data_scope"]["time_to"] == "2005-10-30T09:50:01"
    assert row_dicts(result)[0]["count"] == PER_HOUR


# search_logs (#73) ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("time_from", "time_to", "expected"),
    [
        # Across the fall-back: 01:30 PDT to 01:30 PST, a range no local ts range can name.
        ("2005-10-30T08:30", "2005-10-30T09:30", PER_HOUR),
        # The first and the second copy of the repeated hour, named by their offsets.
        ("2005-10-30T01:00:00-07:00", "2005-10-30T01:00:00-08:00", PER_HOUR),
        ("2005-10-30T01:00:00-08:00", "2005-10-30T02:00:00-08:00", PER_HOUR),
        ("2005-10-30T06:00", "2005-10-30T10:00", STEPS),
    ],
)
def test_search_logs_matches_as_many_log_entries_as_count_by_in_the_same_utc_range(
    fallback: str, time_from: str, time_to: str, expected: int
) -> None:
    window = {"dataset": "bgl", "query": "clock", "time_from": time_from, "time_to": time_to}

    searched = call("search_logs", role="engineer", **window)
    counted = call("count_by", role="engineer", group_by="dataset", **window)

    assert searched["total_matches"] == row_dicts(counted)[0]["count"] == expected
    assert searched["data_scope"]["time_from"] == counted["data_scope"]["time_from"]
    assert searched["data_scope"]["time_to"] == counted["data_scope"]["time_to"]
    assert searched["data_scope"]["time_zone"] == "UTC"
    assert searched["data_scope"]["time_columns"] == {"bgl": "epoch_utc"}


def test_search_logs_reports_first_and_last_seen_in_utc_and_rows_as_logged(fallback: str) -> None:
    result = call(
        "search_logs",
        role="engineer",
        dataset="bgl",
        query="clock",
        time_from="2005-10-30T09:00",
        time_to="2005-10-30T10:00",
    )

    summary = result["summary"]
    assert (summary["first_seen"], summary["last_seen"]) == ("2005-10-30T09:00:00", "2005-10-30T09:50:00")
    # The rows keep ts as logged: 09:00 UTC is the second 01:00, in PST.
    assert [row["ts"] for row in row_dicts(result)] == [f"2005-10-30T01:{m}0:00" for m in range(PER_HOUR)]
    notes = " ".join(result["notes"])
    assert "ts as logged" in notes
    assert "UTC = ts + 7 h until 2005-10-30T09:00 UTC" in notes
    assert "then ts + 8 h" in notes


def test_search_logs_converts_an_input_with_an_offset_and_says_so(fallback: str) -> None:
    result = call(
        "search_logs", role="engineer", dataset="bgl", query="clock", time_from="2005-10-30T01:00:00-08:00"
    )

    assert result["data_scope"]["time_from"] == "2005-10-30T09:00:00"
    assert result["total_matches"] == PER_HOUR
    assert any("converted to UTC" in note for note in result["notes"])


def test_search_logs_resolves_a_relative_range_against_the_utc_corpus_end(fallback: str) -> None:
    # The last Log Entry is 01:50 PST; read as 01:50 PDT, the range would end an hour early.
    result = call("search_logs", role="engineer", dataset="bgl", query="clock", time_from="last 1h")

    assert result["data_scope"]["time_to"] == "2005-10-30T09:50:01"
    assert result["data_scope"]["resolved_against"] == "bgl corpus end 2005-10-30T09:50:00"
    assert result["total_matches"] == PER_HOUR


def test_search_logs_on_openstack_filters_its_utc_ts(fallback: str) -> None:
    result = call(
        "search_logs",
        role="engineer",
        dataset="openstack",
        query="clock",
        time_from="2005-10-30T08:00",
        time_to="2005-10-30T09:00",
    )

    assert result["total_matches"] == PER_HOUR
    assert result["data_scope"]["time_columns"] == {"openstack": "ts"}
    assert result["summary"]["first_seen"] == "2005-10-30T08:00:00"
