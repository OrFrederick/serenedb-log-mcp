"""The pure parts of `trend`: the bucket grid, bucket labels, the bucket limit and drift hand-offs."""

from __future__ import annotations

from datetime import datetime

import pytest

from serenedb_log_mcp.scope import ScopeError
from serenedb_log_mcp.tools.trend import (
    MAX_BUCKETS,
    MAX_SERIES,
    bucket_count,
    bucket_label,
    bucket_start,
    check_buckets,
    handoff_notes,
)

BGL_FIRST = datetime(2005, 6, 3, 15, 42, 50, 363779)
BGL_END = datetime(2006, 1, 4, 8, 0, 6)


def test_the_limits_are_the_decided_ones() -> None:
    assert (MAX_BUCKETS, MAX_SERIES) == (400, 5)


@pytest.mark.parametrize(
    ("bucket", "expected"),
    [
        ("minute", datetime(2005, 6, 3, 15, 42)),
        ("hour", datetime(2005, 6, 3, 15)),
        ("day", datetime(2005, 6, 3)),
        # SereneDB's time_bucket anchors sub-month widths on Monday 2000-01-03.
        ("week", datetime(2005, 5, 30)),
    ],
)
def test_bucket_starts_match_time_bucket(bucket: str, expected: datetime) -> None:
    assert bucket_start(BGL_FIRST, bucket) == expected


def test_the_bucket_count_runs_from_the_first_to_the_last_bucket_touched() -> None:
    start, end = datetime(2005, 6, 1, 9), datetime(2005, 7, 31, 14, 0, 1)

    assert bucket_count(start, end, "day") == 61
    assert bucket_count(start, end, "week") == 9
    # An exclusive end on a bucket boundary does not open a new bucket.
    assert bucket_count(datetime(2005, 6, 1), datetime(2005, 6, 3), "day") == 2


def test_the_whole_bgl_dataset_by_day_fits_and_by_hour_does_not() -> None:
    assert bucket_count(BGL_FIRST, BGL_END, "day") == 216
    check_buckets("bgl", BGL_FIRST, BGL_END, "day")

    with pytest.raises(ScopeError) as caught:
        check_buckets("bgl", BGL_FIRST, BGL_END, "hour")

    message = str(caught.value)
    assert f"{bucket_count(BGL_FIRST, BGL_END, 'hour'):,} hour buckets" in message
    assert "at most 400" in message
    assert "bucket='day' (216 buckets)" in message
    assert "time_from" in message


def test_a_minute_trend_suggests_the_finest_bucket_that_fits() -> None:
    start, end = datetime(2005, 6, 1), datetime(2005, 6, 5)

    with pytest.raises(ScopeError, match=r"bucket='hour' \(96 buckets\)"):
        check_buckets("bgl", start, end, "minute")


@pytest.mark.parametrize(
    ("bucket", "label"),
    [
        ("minute", "2005-06-01T09:05"),
        ("hour", "2005-06-01T09:00"),
        ("day", "2005-06-01"),
        ("week", "2005-06-01"),
    ],
)
def test_bucket_labels_are_as_short_as_the_bucket_allows(bucket: str, label: str) -> None:
    value = (
        datetime(2005, 6, 1, 9, 5)
        if bucket == "minute"
        else datetime(2005, 6, 1, 9 if bucket == "hour" else 0)
    )

    assert bucket_label(value, bucket) == label


def test_a_series_with_a_drift_partner_gets_a_handoff_note_naming_it() -> None:
    rows = [
        ("f0f0f0f0f0f0f0f0", "a0a0a0a0a0a0a001", 60, datetime(2005, 6, 1, 10), datetime(2005, 6, 30, 14)),
        ("f0f0f0f0f0f0f0f0", "a0a0a0a0a0a0a002", 62, datetime(2005, 7, 1, 10), datetime(2005, 7, 31, 14)),
        (
            "f1f1f1f1f1f1f1f1",
            "b0b0b0b0b0b0b003",
            100,
            datetime(2005, 7, 20, 10),
            datetime(2005, 7, 20, 10, 50),
        ),
    ]

    notes = handoff_notes(["a0a0a0a0a0a0a001", "b0b0b0b0b0b0b003", "(no Pattern)"], rows)

    assert set(notes) == {"a0a0a0a0a0a0a001"}
    note = notes["a0a0a0a0a0a0a001"]
    assert "a0a0a0a0a0a0a002" in note
    assert "62 Log Entries in the whole Dataset" in note
    assert "2005-07-01T10:00:00" in note
    assert "family_id='f0f0f0f0f0f0f0f0'" in note
    assert "wording" in note


def test_without_pattern_families_there_are_no_handoff_notes() -> None:
    assert handoff_notes(["a0a0a0a0a0a0a001"], []) == {}
