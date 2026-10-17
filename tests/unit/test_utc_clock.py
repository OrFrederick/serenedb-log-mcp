"""One clock for time arithmetic (#65): UTC, from epoch_utc where ts is local time.

BGL's ts is US Pacific local time: on 2005-10-30 the hour from 01:00 repeats, so a bucket on ts
holds two hours of Log Entries. These checks need no database: the SQL the aggregation tools build,
the conversion of a Dataset's logged first and last Log Entry to UTC, and the time inputs.
"""

from __future__ import annotations

from datetime import datetime

import anyio
import pytest

from serenedb_log_mcp import clock
from serenedb_log_mcp.baseline import window
from serenedb_log_mcp.corpus.datasets import DATASETS, relation
from serenedb_log_mcp.evidence import DataScope
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools import latency_stats
from serenedb_log_mcp.tools._aggregate import EntryFilters, Part, stats_statement
from serenedb_log_mcp.tools.trend import bucket_statement, family_statement

BGL, OPENSTACK = DATASETS["bgl"], DATASETS["openstack"]
EPOCH_TIMESTAMP = "make_timestamp(CAST(round("


def part(dataset: str, first: datetime, last: datetime) -> Part:
    return Part(relation(dataset), window(dataset, first, last, first, last), None, EntryFilters())


def bgl_part() -> Part:
    return part("bgl", datetime(2005, 10, 30, 6), datetime(2005, 10, 30, 10))


def openstack_part() -> Part:
    return part("openstack", datetime(2017, 5, 14, 19), datetime(2017, 5, 17, 12))


def test_bgl_time_arithmetic_reads_epoch_utc_and_openstack_reads_its_utc_ts() -> None:
    assert clock.time_column(BGL) == "epoch_utc"
    assert clock.time_column(OPENSTACK) == "ts"

    bgl = clock.utc_time(relation("bgl")).as_string(None)
    assert EPOCH_TIMESTAMP in bgl
    assert '"epoch_utc"' in bgl
    assert '"ts"' not in bgl
    assert clock.utc_time(relation("openstack")).as_string(None) == 'e."ts"'


def test_trend_buckets_bgl_on_epoch_utc_and_never_on_local_ts() -> None:
    text = bucket_statement(bgl_part(), ["(all)"], "hour").text

    assert EPOCH_TIMESTAMP in text
    assert 'e."ts"' not in text


def test_openstack_buckets_stay_on_its_ts_which_is_utc() -> None:
    text = bucket_statement(openstack_part(), ["(all)"], "hour").text

    assert EPOCH_TIMESTAMP not in text
    assert 'e."ts"' in text


def test_the_baselines_and_the_time_range_run_on_the_same_clock_as_the_buckets() -> None:
    p = bgl_part()

    stats = stats_statement([p], {"bgl": ["(all)"]}, {"bgl": ["(all)"]}).text
    assert EPOCH_TIMESTAMP in stats
    assert 'e."ts"' not in stats  # first_seen, last_seen, weeks, in_scope and in_prev alike
    assert 'e."ts"' not in p.between(datetime(2005, 10, 30, 8), datetime(2005, 10, 30, 9)).text
    family = family_statement(p, ["a0a0a0a0a0a0a001"]).text
    assert '"ts"' not in family


@pytest.mark.parametrize(
    ("logged", "latest", "utc"),
    [
        # PDT, UTC-7: the first BGL Log Entry.
        (datetime(2005, 6, 3, 15, 42, 50, 363779), False, datetime(2005, 6, 3, 22, 42, 50, 363779)),
        # PST, UTC-8: the last BGL Log Entry.
        (datetime(2006, 1, 4, 8, 0, 5, 233639), True, datetime(2006, 1, 4, 16, 0, 5, 233639)),
        # 01:50 happens twice on 2005-10-30: a first Log Entry is the earlier one, a last the later.
        (datetime(2005, 10, 30, 1, 50), False, datetime(2005, 10, 30, 8, 50)),
        (datetime(2005, 10, 30, 1, 50), True, datetime(2005, 10, 30, 9, 50)),
    ],
)
def test_a_logged_bgl_time_converts_to_utc_and_the_repeated_hour_widens_the_range(
    logged: datetime, latest: bool, utc: datetime
) -> None:
    assert clock.to_utc(BGL, logged, latest=latest) == utc


def test_a_utc_dataset_time_is_already_utc() -> None:
    logged = datetime(2017, 5, 14, 19, 39, 1, 445000)

    assert clock.to_utc(OPENSTACK, logged) == logged
    assert clock.to_utc(OPENSTACK, logged, latest=True) == logged


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2005-10-30T01:00:00-07:00", "2005-10-30T08:00:00"),
        ("2005-10-30T01:00:00-08:00", "2005-10-30T09:00:00"),
        ("2017-05-16T20:00:00Z", "2017-05-16T20:00:00"),
        (" 2017-05-16T22:00:00+02:00 ", "2017-05-16T20:00:00"),
    ],
)
def test_a_time_input_with_an_offset_is_converted_to_utc_with_a_note(given: str, expected: str) -> None:
    value, note = clock.utc_input("time_from", given)

    assert value == expected
    assert note is not None
    assert repr(given) in note
    assert expected in note


@pytest.mark.parametrize("given", [None, "2005-07-01", "2005-07-01T12:00:00", "last 7d", "yesterday"])
def test_other_time_inputs_pass_unchanged(given: str | None) -> None:
    # Naive times are UTC already; relative and invalid inputs are scope.py's to resolve or refuse.
    assert clock.utc_input("time_to", given) == (given, None)


def test_the_note_says_how_to_turn_a_logged_bgl_ts_into_utc() -> None:
    note = clock.clock_note(
        {
            "bgl": (datetime(2005, 6, 3, 22, 42, 50), datetime(2006, 1, 4, 16, 0, 6)),
            "openstack": (datetime(2017, 5, 14, 19, 39, 1), datetime(2017, 5, 17, 12, 2, 36)),
        }
    )

    assert "UTC" in note
    assert "US Pacific" in note
    assert "epoch_utc" in note
    assert "ts + 7 h until 2005-10-30T09:00 UTC" in note
    assert "ts + 8 h" in note
    assert "openstack" in note


def test_a_dataset_scope_names_its_clock_and_time_columns() -> None:
    scope = DataScope(
        datasets=["bgl", "openstack"], time_zone="UTC", time_columns={"bgl": "epoch_utc", "openstack": "ts"}
    )

    dumped = scope.model_dump()
    assert dumped["time_zone"] == "UTC"
    assert dumped["time_columns"] == {"bgl": "epoch_utc", "openstack": "ts"}
    assert DataScope(datasets=["bgl"]).time_zone is None


def test_latency_stats_buckets_ts_because_its_dataset_logs_utc() -> None:
    # latency_stats buckets OpenStack's ts directly; that is UTC only while OpenStack logs UTC.
    assert latency_stats.DATASET == "openstack"
    assert DATASETS[latency_stats.DATASET].ts_is_local is False


@pytest.mark.parametrize("tool", ["count_by", "trend"])
def test_the_aggregation_tools_say_their_times_are_utc(tool: str) -> None:
    listed = {t.name: t for t in anyio.run(create_server().list_tools)}[tool]
    description = " ".join((listed.description or "").split())
    properties = listed.input_schema["properties"]

    assert "UTC for every Dataset" in description
    assert "epoch_utc" in description
    assert "in UTC for every Dataset" in properties["time_from"]["description"]
    assert "UTC" in properties["time_to"]["description"]
