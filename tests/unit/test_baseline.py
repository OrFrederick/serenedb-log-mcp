"""`baseline.py`: the Data Scope window per Dataset, the previous period and the normal/unusual flag."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from serenedb_log_mcp.baseline import (
    MIN_DIFFERENCE,
    MIN_WEEKS,
    UNUSUAL_RATIO,
    dataset_end,
    few_weeks_notes,
    judge,
    method_note,
    window,
)

FIRST = datetime(2005, 6, 3, 15, 42, 50, 363779)
LAST = datetime(2006, 1, 4, 8, 0, 5, 233639)
END = datetime(2006, 1, 4, 8, 0, 6)


# Windows --------------------------------------------------------------------------------------------


def test_the_dataset_end_is_the_last_second_plus_one_like_relative_times() -> None:
    assert dataset_end(LAST) == END


def test_no_time_range_covers_the_whole_dataset_and_has_no_previous_period() -> None:
    w = window("bgl", FIRST, LAST)

    assert (w.start, w.end) == (FIRST, END)
    assert w.previous is None
    assert w.previous_problem is not None
    assert "first Log Entry" in w.previous_problem


def test_a_time_range_inside_the_dataset_gets_the_previous_period_of_equal_length() -> None:
    start, end = datetime(2005, 11, 8), datetime(2005, 11, 15)

    w = window("bgl", FIRST, LAST, start, end)

    assert (w.start, w.end) == (start, end)
    assert w.days == 7
    assert w.previous == (datetime(2005, 11, 1), start)
    assert w.previous_days == 7
    assert w.previous_problem is None


def test_an_open_end_runs_to_the_dataset_end() -> None:
    start = END - timedelta(days=7)

    w = window("bgl", FIRST, LAST, start, None)

    assert w.end == END
    assert w.previous == (start - timedelta(days=7), start)


def test_a_range_wider_than_the_dataset_is_cut_to_the_dataset() -> None:
    w = window("bgl", FIRST, LAST, datetime(2005, 1, 1), datetime(2007, 1, 1))

    assert (w.start, w.end) == (FIRST, END)
    assert w.previous is None


def test_a_previous_period_that_starts_before_the_first_log_entry_is_null_with_the_reason() -> None:
    w = window("bgl", FIRST, LAST, datetime(2005, 6, 10), datetime(2005, 6, 20))

    assert w.previous is None
    assert w.previous_problem is not None
    assert "2005-05-31" in w.previous_problem


def test_an_explicit_baseline_period_replaces_the_previous_period() -> None:
    baseline = (datetime(2005, 7, 1), datetime(2005, 8, 1))

    w = window("bgl", FIRST, LAST, datetime(2005, 11, 8), datetime(2005, 11, 15), baseline)

    assert w.previous == baseline
    assert w.previous_days == 31


def test_an_explicit_baseline_outside_the_dataset_is_null_with_the_reason() -> None:
    w = window("bgl", FIRST, LAST, None, None, (datetime(2005, 5, 1), datetime(2005, 6, 1)))

    assert w.previous is None
    assert w.previous_problem is not None
    assert "not inside" in w.previous_problem


def test_a_range_outside_the_dataset_is_empty() -> None:
    first, last = datetime(2017, 5, 14), datetime(2017, 5, 17)

    w = window("openstack", first, last, datetime(2005, 6, 1), datetime(2005, 7, 1))

    assert w.days == 0
    assert w.previous is None


# Judgement ------------------------------------------------------------------------------------------


def weekly_flag(count: int, median_week: float, days: float = 7) -> str:
    """The flag for a Dataset with 30 whole weeks and no previous period."""
    return judge(count, median_week=median_week, weeks=30, days=days, previous=None, previous_days=None).flag


def test_the_median_week_scaled_to_the_scope_is_the_expected_value() -> None:
    verdict = judge(140, median_week=100, weeks=30, days=7, previous=None, previous_days=None)

    assert verdict.expected == 100
    assert verdict.basis == "median_week"
    assert verdict.flag == "normal"


def test_a_count_at_least_three_times_the_expected_value_is_unusual_high() -> None:
    verdict = judge(300, median_week=50, weeks=30, days=14, previous=None, previous_days=None)

    assert verdict.expected == 100
    assert verdict.flag == "unusual_high"


def test_a_count_at_most_a_third_of_the_expected_value_is_unusual_low() -> None:
    assert weekly_flag(33, median_week=100) == "unusual_low"


def test_a_tiny_difference_is_normal_even_when_the_ratio_is_large() -> None:
    # 9 against an expected 0 is "infinitely" more, but fewer than MIN_DIFFERENCE Log Entries.
    assert weekly_flag(MIN_DIFFERENCE - 1, median_week=0) == "normal"
    assert weekly_flag(MIN_DIFFERENCE, median_week=0) == "unusual_high"


def test_a_storm_in_a_category_whose_normal_week_is_zero_is_unusual_high() -> None:
    verdict = judge(152_734, median_week=0, weeks=30, days=214.7, previous=None, previous_days=None)

    assert verdict.expected == 0
    assert verdict.flag == "unusual_high"


def test_too_few_whole_weeks_fall_back_to_the_previous_period_scaled_to_the_scope() -> None:
    verdict = judge(400, median_week=90, weeks=MIN_WEEKS - 1, days=1, previous=200, previous_days=2)

    assert verdict.basis == "previous_period"
    assert verdict.expected == 100
    assert verdict.flag == "unusual_high"


def test_without_either_baseline_the_flag_says_so() -> None:
    verdict = judge(400, median_week=None, weeks=0, days=2.7, previous=None, previous_days=None)

    assert verdict.expected is None
    assert verdict.basis is None
    assert verdict.flag == "no_baseline"


def test_the_thresholds_are_the_documented_ones() -> None:
    assert (UNUSUAL_RATIO, MIN_DIFFERENCE, MIN_WEEKS) == (3, 10, 4)


# Method note ----------------------------------------------------------------------------------------


def test_the_method_note_states_the_periods_weeks_and_thresholds() -> None:
    bgl = window("bgl", FIRST, LAST, datetime(2005, 11, 8), datetime(2005, 11, 15))
    openstack = window("openstack", datetime(2017, 5, 14, 19), datetime(2017, 5, 17, 12))

    note = method_note([bgl, openstack], {"bgl": 30, "openstack": 0})

    assert "2005-11-01T00:00:00 to 2005-11-08T00:00:00" in note
    assert "bgl 30" in note
    assert "openstack 0" in note
    assert "at least 3 times expected" in note
    assert "10" in note
    assert "no_baseline" in note


@pytest.mark.parametrize("flag", ["normal", "unusual_high", "unusual_low", "no_baseline"])
def test_every_flag_is_explained_in_the_method_note(flag: str) -> None:
    assert flag in method_note([window("bgl", FIRST, LAST)], {"bgl": 30})


def test_the_method_note_says_the_median_week_comes_from_outside_the_data_scope() -> None:
    note = method_note([window("bgl", FIRST, LAST)], {"bgl": 0})

    assert "whole weeks outside the Data Scope (bgl 0)" in note


def test_too_few_weeks_outside_the_scope_get_a_note_that_points_to_trend() -> None:
    whole = window("bgl", FIRST, LAST)
    week = window("bgl", FIRST, LAST, datetime(2005, 11, 7), datetime(2005, 11, 14))

    notes = few_weeks_notes([whole], {"bgl": 0})
    assert len(notes) == 1
    assert "fewer than 4 whole weeks outside the Data Scope" in notes[0]
    assert "trend" in notes[0]
    assert few_weeks_notes([week], {"bgl": 29}) == []
