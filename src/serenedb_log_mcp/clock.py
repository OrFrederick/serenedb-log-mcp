"""One clock for time arithmetic: UTC (#65; the #1 Decision proposal puts time arithmetic on UTC).

BGL logs `ts` in US Pacific local time with daylight saving time. On 2005-10-30 the hour from 01:00
happens twice, so a bucket, window or week on `ts` can hold two hours of Log Entries, or one hour
less than it says. `epoch_utc` is unambiguous. The rule every tool that does time arithmetic follows:

- comparisons, windows, buckets and baseline periods run on UTC: on `epoch_utc` for a Dataset whose
  `ts` is local time (`Dataset.ts_is_local`), on `ts` itself for a Dataset that logs UTC (OpenStack;
  remote Datasets without an `epoch_utc` column);
- time inputs are read as UTC (an input with an offset is converted), and every time the tool
  computes is reported in UTC; the Data Scope says so in `time_zone` and `time_columns`.

The Datasets' first and last Log Entries (`corpus.facts`, read on `ts`) are converted with the
Dataset's `ts_zone`. In the repeated hour a logged time is ambiguous, so a first Log Entry takes the
earlier instant and a last one the later: the range then always holds every Log Entry.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from psycopg import sql

from serenedb_log_mcp.corpus.datasets import Dataset, Relation, dataset

__all__ = [
    "UTC_ZONE",
    "clock_note",
    "time_column",
    "time_columns",
    "to_utc",
    "utc_input",
    "utc_range",
    "utc_time",
]

UTC_ZONE = "UTC"
"""The clock of every time the aggregation tools take and report."""


def time_column(entry: Dataset) -> str:
    """The canonical column a Dataset's time arithmetic reads: epoch_utc where ts is local time."""
    return "epoch_utc" if entry.ts_is_local else "ts"


def time_columns(names: list[str]) -> dict[str, str]:
    """`time_column` per Dataset name, for the Data Scope."""
    return {name: time_column(dataset(name)) for name in names}


def utc_time(rel: Relation, alias: str = "e") -> sql.Composable:
    """A Log Entry's time as a UTC TIMESTAMP: `ts` where it is UTC, else built from `epoch_utc`.

    `make_timestamp(microseconds)` is SereneDB's epoch-to-TIMESTAMP function (it has no
    `to_timestamp`); `epoch_utc` holds seconds with the logged microseconds as a fraction.
    """
    if not rel.dataset.ts_is_local:
        return sql.SQL(".").join([sql.SQL(alias), rel.column("time")])
    epoch = sql.SQL(".").join([sql.SQL(alias), rel.column("epoch")])
    return sql.SQL("make_timestamp(CAST(round({} * 1000000) AS BIGINT))").format(epoch)


def to_utc(entry: Dataset, logged: datetime, *, latest: bool = False) -> datetime:
    """A naive `ts` as logged in `entry`'s zone, as a naive UTC datetime.

    A time in the repeated fall-back hour happened twice: `latest` picks the later instant (for a
    last Log Entry), otherwise the earlier one (for a first Log Entry).
    """
    if not entry.ts_is_local:
        return logged
    local = logged.replace(tzinfo=ZoneInfo(entry.ts_zone), fold=1 if latest else 0)
    return local.astimezone(UTC).replace(tzinfo=None)


def utc_range(name: str, first: datetime, last: datetime) -> tuple[datetime, datetime]:
    """A Dataset's first and last Log Entry, read on ts, in UTC; the range holds every Log Entry."""
    entry = dataset(name)
    return to_utc(entry, first), to_utc(entry, last, latest=True)


def utc_input(name: str, value: str | None) -> tuple[str | None, str | None]:
    """A time input as UTC, and a note when it was converted.

    An ISO-8601 time with an offset (`-07:00`, `Z`) becomes the naive UTC time; anything else is
    returned unchanged, because a naive time is UTC already and `scope.resolve_time` resolves or
    refuses relative and invalid inputs.
    """
    if value is None:
        return None, None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return value, None
    if parsed.tzinfo is None:
        return value, None
    converted = parsed.astimezone(UTC).replace(tzinfo=None).isoformat()
    return converted, f"{name}={value!r} has an offset, so it was converted to UTC: {converted}."


def clock_note(ranges: Mapping[str, tuple[datetime, datetime]]) -> str:
    """The note that says every time is UTC and, per Dataset, which column the arithmetic ran on.

    `ranges` maps each Dataset to its first and last Log Entry in UTC; for a Dataset that logs local
    time the note gives the offset to add to a logged `ts`, with the UTC instants it changes at.
    """
    parts = [
        "Every time here is UTC: time_from, time_to, the buckets, first_seen, last_seen and the "
        "baseline periods, and time inputs are read as UTC (an input with an offset is converted)."
    ]
    for name, (first, last) in ranges.items():
        entry = dataset(name)
        if not entry.ts_is_local:
            parts.append(f"{name}: {entry.time_note}")
            continue
        parts.append(
            f"{name}: {entry.time_note} Its time range, buckets and baselines run on epoch_utc too. "
            f"For a logged {name} ts, {_offsets(entry, first, last)}."
        )
    return " ".join(parts)


def _offsets(entry: Dataset, first: datetime, last: datetime) -> str:
    """How a logged ts turns into UTC over [first, last] (UTC), for example 'UTC = ts + 7 h until
    2005-10-30T09:00 UTC (when ts went from 02:00 to 01:00), then ts + 8 h'."""
    zone = ZoneInfo(entry.ts_zone)

    def offset(at: datetime) -> timedelta:
        found = at.replace(tzinfo=UTC).astimezone(zone).utcoffset()
        return found if found is not None else timedelta(0)

    at = first.replace(minute=0, second=0, microsecond=0)
    current = offset(at)
    said: list[str] = []
    while at <= last:  # hourly: the zones' changes fall on the hour
        now = offset(at)
        if now != current:
            before, after = (at + current).strftime("%H:%M"), (at + now).strftime("%H:%M")
            said.append(
                f"ts {_signed(-current)} until {at.strftime('%Y-%m-%dT%H:%M')} UTC "
                f"(when ts went from {before} to {after})"
            )
            current = now
        at += timedelta(hours=1)
    said.append(f"ts {_signed(-current)}")
    return "UTC = " + ", then ".join(said)


def _signed(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"{sign} {hours} h" + (f" {rest} min" if rest else "")
