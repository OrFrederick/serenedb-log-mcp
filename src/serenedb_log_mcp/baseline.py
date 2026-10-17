"""Baselines on every number (docs/decisions.md, differentiator 7): pure rules, no SQL.

A count means little without a reference. Next to every count, `count_by` and `trend` report:

- `prev_period`: the same count in the period of equal length just before the Data Scope, or in
  the baseline period the caller named; null when that period is not inside the Dataset;
- `median_week`: the median of the zero-filled weekly counts (Monday-start weeks, as SereneDB's
  `time_bucket` lays them out) over the Dataset's whole weeks outside the Data Scope, with the same
  filters, so a count is never judged against the weeks it is made of;
- `expected`: what a normal period of the Data Scope's length holds: `median_week` scaled to the
  Data Scope's length, or the previous period scaled to it when fewer than `MIN_WEEKS` whole weeks
  lie outside the Data Scope (OpenStack spans under three days; a whole-Dataset scope leaves none);
- `baseline_flag`: `unusual_high`, `unusual_low`, `normal` or `no_baseline`, by a fixed ratio and a
  minimum absolute difference, so 9 Log Entries against an expected 0 is not "unusual".

The SQL that counts lives with the tools (`tools/_aggregate.py`); this module decides the windows
and the verdict, so both can be tested without a database.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

__all__ = [
    "MIN_DIFFERENCE",
    "MIN_WEEKS",
    "UNUSUAL_RATIO",
    "WEEK",
    "DatasetWindow",
    "Flag",
    "Verdict",
    "dataset_end",
    "few_weeks_notes",
    "judge",
    "method_note",
    "window",
]

UNUSUAL_RATIO = 3
"""A count is unusual when it is at least this many times the expected value, or at most 1/this."""
MIN_DIFFERENCE = 10
"""...and differs from the expected value by at least this many Log Entries."""
MIN_WEEKS = 4
"""Fewest whole weeks a Dataset needs before its median week is used as the baseline."""
WEEK = timedelta(days=7)
DAY = timedelta(days=1)

Flag = Literal["normal", "unusual_high", "unusual_low", "no_baseline"]
Basis = Literal["median_week", "previous_period"]


def dataset_end(last: datetime) -> datetime:
    """The exclusive end of a Dataset: its last Log Entry cut to the second, plus one second.

    The same end `scope.resolve_time` gives a relative time such as 'last 7d'.
    """
    return last.replace(microsecond=0) + timedelta(seconds=1)


@dataclass(frozen=True)
class DatasetWindow:
    """The part of the Data Scope that falls inside one Dataset, and its previous period."""

    dataset: str
    first: datetime
    """The Dataset's first Log Entry."""
    dataset_end: datetime
    """The Dataset's exclusive end (`dataset_end(last)`)."""
    start: datetime
    """Start of the Data Scope inside the Dataset, inclusive."""
    end: datetime
    """End of the Data Scope inside the Dataset, exclusive; equal to `start` when they do not overlap."""
    previous: tuple[datetime, datetime] | None
    """The previous or named baseline period, start inclusive and end exclusive; None when unusable."""
    previous_problem: str | None
    """Why `previous` is None, in one sentence for the notes."""

    @property
    def days(self) -> float:
        """Length of the Data Scope inside the Dataset, in days."""
        return (self.end - self.start) / DAY

    @property
    def previous_days(self) -> float | None:
        """Length of the previous period, in days."""
        return None if self.previous is None else (self.previous[1] - self.previous[0]) / DAY


def window(
    dataset: str,
    first: datetime,
    last: datetime,
    start: datetime | None = None,
    end: datetime | None = None,
    baseline: tuple[datetime, datetime] | None = None,
) -> DatasetWindow:
    """The Data Scope [start, end) cut to one Dataset, with its previous or named baseline period.

    `first` and `last` are the Dataset's first and last Log Entries; `start` and `end` are the Data
    Scope's resolved range (None for the Dataset's own bound); `baseline` is the caller's baseline
    period, which replaces the default previous period of equal length.
    """
    ds_end = dataset_end(last)
    inside_start = first if start is None else max(start, first)
    inside_end = ds_end if end is None else min(end, ds_end)
    inside_end = max(inside_end, inside_start)
    previous, problem = _previous(dataset, first, ds_end, start, inside_start, inside_end, baseline)
    return DatasetWindow(dataset, first, ds_end, inside_start, inside_end, previous, problem)


def _previous(
    dataset: str,
    first: datetime,
    ds_end: datetime,
    start: datetime | None,
    inside_start: datetime,
    inside_end: datetime,
    baseline: tuple[datetime, datetime] | None,
) -> tuple[tuple[datetime, datetime] | None, str | None]:
    bounds = f"{dataset} has Log Entries from {first.isoformat()} to {ds_end.isoformat()}"
    if baseline is not None:
        begin, finish = baseline
        if begin < first or finish > ds_end:
            return None, (
                f"the baseline period {begin.isoformat()} to {finish.isoformat()} is not inside the "
                f"{dataset} Log Entries ({bounds}), so its count would be incomplete"
            )
        return baseline, None
    if start is None or start < first:
        return None, (
            f"the Data Scope starts at the first Log Entry of {dataset}, so there is no previous period"
        )
    length = inside_end - inside_start
    if length <= timedelta(0):
        return None, f"the Data Scope does not overlap the {dataset} Log Entries ({bounds})"
    previous = (inside_start - length, inside_start)
    if previous[0] < first:
        return None, (
            f"the previous period {previous[0].isoformat()} to {previous[1].isoformat()} starts "
            f"before the first Log Entry of {dataset} ({bounds}), so its count would be incomplete"
        )
    return previous, None


@dataclass(frozen=True)
class Verdict:
    """The baseline verdict on one count."""

    expected: float | None
    basis: Basis | None
    flag: Flag


def judge(
    count: int,
    *,
    median_week: float | None,
    weeks: int,
    days: float,
    previous: int | None,
    previous_days: float | None,
) -> Verdict:
    """Compare `count` over `days` with what a normal period of that length holds."""
    expected: float
    basis: Basis
    if median_week is not None and weeks >= MIN_WEEKS:
        expected, basis = median_week * days / 7, "median_week"
    elif previous is not None and previous_days:
        expected, basis = previous * days / previous_days, "previous_period"
    else:
        return Verdict(None, None, "no_baseline")
    flag: Flag = "normal"
    if count >= UNUSUAL_RATIO * expected and count - expected >= MIN_DIFFERENCE:
        flag = "unusual_high"
    elif count * UNUSUAL_RATIO <= expected and expected - count >= MIN_DIFFERENCE:
        flag = "unusual_low"
    return Verdict(round(expected, 1), basis, flag)


def method_note(windows: Sequence[DatasetWindow], whole_weeks: Mapping[str, int]) -> str:
    """One note that states how prev_period, median_week, expected and baseline_flag were found."""
    periods = "; ".join(
        f"{w.dataset}: {w.previous[0].isoformat()} to {w.previous[1].isoformat()}"
        if w.previous is not None
        else f"{w.dataset}: null, because {w.previous_problem}"
        for w in windows
    )
    weeks = ", ".join(f"{w.dataset} {whole_weeks.get(w.dataset, 0)}" for w in windows)
    return (
        "Baselines, per row: prev_period is the count with the same filters in the period of equal "
        f"length just before the Data Scope, or in the named baseline period ({periods}). "
        "median_week is the median of the zero-filled weekly counts (Monday-start weeks) with the "
        f"same filters over the Dataset's whole weeks outside the Data Scope ({weeks}). expected is "
        "median_week scaled to "
        "the Data Scope's length, or prev_period scaled to it when fewer than "
        f"{MIN_WEEKS} such weeks exist. baseline_flag is unusual_high when the count is at least "
        f"{UNUSUAL_RATIO} times expected and at least {MIN_DIFFERENCE} Log Entries above it, unusual_low "
        f"when it is at most expected / {UNUSUAL_RATIO} and at least {MIN_DIFFERENCE} below it, "
        "normal otherwise, and no_baseline when neither baseline exists."
    )


def few_weeks_notes(windows: Sequence[DatasetWindow], whole_weeks: Mapping[str, int]) -> list[str]:
    """Say where the median week is missing because too few whole weeks lie outside the Data Scope.

    A Data Scope that covers the whole Dataset leaves no week to compare with: judging it against
    its own weeks would call every bursty group "unusual".
    """
    return [
        f"{w.dataset} has fewer than {MIN_WEEKS} whole weeks outside the Data Scope "
        f"({whole_weeks.get(w.dataset, 0)}), so median_week is null there and the flag falls back to "
        "the previous period, or is no_baseline without one. To see which weeks stand out, call "
        f"trend over {w.dataset}, or narrow the time range."
        for w in windows
        if whole_weeks.get(w.dataset, 0) < MIN_WEEKS
    ]
