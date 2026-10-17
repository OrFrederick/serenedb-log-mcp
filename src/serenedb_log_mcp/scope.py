"""Builds T3's `DataScope` from tool inputs (plan sections 2.3 and 2.7).

It validates and resolves; it never queries. The tool supplies what only the database knows (the
valid Components and levels, the Datasets' last timestamps), usually from T4's corpus registry or
the `describe_corpus` cache, and binds the resolved values in its own SQL.

- Enum inputs are checked against the valid values; a near miss gets a "did you mean" hint
  (`KERNAL` suggests `KERNEL`), and a value in another case resolves to the canonical one.
- Relative times such as `last 7d` resolve against the Dataset's last timestamp, not today
  (`docs/decisions.md`, corpus-relative time); the Data Scope records the input and the range.
- `pattern_ids[]` (16-hex `template_id` values) and `family_id` are the shared filters every
  Evidence tool accepts.

Every input error is a `ScopeError`, a `ToolError` whose text tells the agent how to fix the call.
A tool that fails to pass `corpus_end` for a relative time has a bug, not a bad input, and gets a
`toolkit.ToolContractError` instead.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp.evidence import DataScope, ScopeFilters
from serenedb_log_mcp.toolkit import ToolContractError

__all__ = [
    "FAMILY_ID_RE",
    "PATTERN_ID_RE",
    "ScopeError",
    "TimeRange",
    "build_scope",
    "resolve_time",
    "time_bounds",
    "validate_choice",
    "validate_choices",
    "validate_family_id",
    "validate_pattern_ids",
]

PATTERN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
"""A Pattern id: the first 16 hex characters of an md5 (plan T9)."""
FAMILY_ID_RE = re.compile(r"^[0-9a-f]{16}(?:[0-9a-f]{16})?$")
"""A Pattern Family id: an md5 in hex, full or cut to 16 characters like `template_id` (T9 decides)."""

MAX_LISTED = 20
"""How many valid values an error message lists before it says how many more there are."""

_UNITS = {
    "m": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "mins": timedelta(minutes=1),
    "minute": timedelta(minutes=1),
    "minutes": timedelta(minutes=1),
    "h": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "hrs": timedelta(hours=1),
    "hour": timedelta(hours=1),
    "hours": timedelta(hours=1),
    "d": timedelta(days=1),
    "day": timedelta(days=1),
    "days": timedelta(days=1),
    "w": timedelta(weeks=1),
    "wk": timedelta(weeks=1),
    "wks": timedelta(weeks=1),
    "week": timedelta(weeks=1),
    "weeks": timedelta(weeks=1),
}
_RELATIVE_RE = re.compile(r"^(?:last|past)\s+(?:(\d+)\s*)?([a-z]+)$")
_LOOKS_RELATIVE_RE = re.compile(r"^(?:last|past)\b")

_ISO_EXAMPLES = "'2005-07-01' or '2005-07-01T12:00:00'"
_RELATIVE_HELP = (
    "Use 'last <N><unit>' with N of at least 1 and the unit m, h, d or w, for example 'last 24h', "
    "'last 7d', 'last 2w' or 'last 30d' for a month."
)


class ScopeError(ToolError):
    """A tool input that does not name a valid Data Scope; the text says how to fix it."""


# Enum inputs ------------------------------------------------------------------------------------------


def validate_choice(name: str, value: str, allowed: Iterable[str]) -> str:
    """`value` as one of `allowed`: exact, or the one value that differs only in case.

    Raises ScopeError with "did you mean" suggestions, or the valid values when nothing is close.
    """
    options = list(dict.fromkeys(allowed))
    problem = _choice_problem(name, value, options)
    if isinstance(problem, str):
        raise ScopeError(problem)
    return problem[0]


def validate_choices(name: str, values: Sequence[str] | None, allowed: Iterable[str]) -> list[str] | None:
    """Several values of one enum input, canonical and deduplicated in order; None when none given.

    Every unknown value is reported in one ScopeError, so the agent fixes the call in one go.
    """
    if not values:
        return None
    options = list(dict.fromkeys(allowed))
    resolved: list[str] = []
    problems: list[str] = []
    for value in values:
        outcome = _choice_problem(name, value, options)
        if isinstance(outcome, str):
            problems.append(outcome)
        elif outcome[0] not in resolved:
            resolved.append(outcome[0])
    if problems:
        raise ScopeError(" ".join(problems))
    return resolved


def _choice_problem(name: str, value: str, options: list[str]) -> tuple[str] | str:
    """`(canonical,)` when `value` is valid, otherwise the error text."""
    if value in options:
        return (value,)
    same = [option for option in options if option.casefold() == value.casefold()]
    if len(same) == 1:
        return (same[0],)
    if same:
        return f"{name}={value!r} matches {_or(same)} in different cases; pass one of them exactly."
    by_fold: dict[str, str] = {}
    for option in options:
        by_fold.setdefault(option.casefold(), option)
    close = difflib.get_close_matches(value.casefold(), list(by_fold), n=3, cutoff=0.6)
    if close:
        return (
            f"{name}={value!r} is not a known value. Did you mean {_or([by_fold[c] for c in close])}? "
            "Call describe_corpus for the valid values."
        )
    listed = ", ".join(repr(option) for option in options[:MAX_LISTED])
    more = len(options) - MAX_LISTED
    if more > 0:
        listed += f" and {more} more"
    return (
        f"{name}={value!r} is not a known value. Valid values: {listed or 'none'}. "
        "Call describe_corpus for the Datasets, Components and levels."
    )


def _or(values: Sequence[str]) -> str:
    quoted = [repr(value) for value in values]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"


# Shared Pattern filters -----------------------------------------------------------------------------


def validate_pattern_ids(values: Sequence[str] | None) -> list[str] | None:
    """Pattern ids as lowercase 16-hex `template_id` values, deduplicated; None when none given."""
    if not values:
        return None
    normal = [value.strip().lower() for value in values]
    bad = [value for value, id_ in zip(values, normal, strict=True) if not PATTERN_ID_RE.fullmatch(id_)]
    if bad:
        raise ScopeError(
            f"pattern_ids: {', '.join(repr(value) for value in bad)} "
            f"{'is not a Pattern id' if len(bad) == 1 else 'are not Pattern ids'}. Pattern ids are the "
            "16-hex-character template_id values that list_patterns returns, for example "
            "'3f2a9c0d1b4e5f67'; alert categories such as 'KERNDTLB' and Loghub EventIds such as "
            "'E218' are not Pattern ids."
        )
    return list(dict.fromkeys(normal))


def validate_family_id(value: str | None) -> str | None:
    """A Pattern Family id in lowercase hex; None when none given."""
    if value is None:
        return None
    normal = value.strip().lower()
    if not FAMILY_ID_RE.fullmatch(normal):
        raise ScopeError(
            f"family_id={value!r} is not a Pattern Family id. Family ids are the hex family_id values "
            "that list_patterns returns next to each Pattern."
        )
    return normal


# Time -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TimeRange:
    """A resolved time range: `start` inclusive, `end` exclusive, None for the Dataset's own bound.

    `time_input` is the relative input as given and `resolved_against` names the Dataset end it was
    resolved against; both are None for absolute times.
    """

    start: datetime | None
    end: datetime | None
    time_input: str | None
    resolved_against: str | None


def resolve_time(
    time_from: str | None,
    time_to: str | None,
    *,
    datasets: Sequence[str] = (),
    corpus_end: Mapping[str, datetime] | None = None,
) -> TimeRange:
    """Resolve the time inputs of a tool call.

    Absolute times are ISO-8601 without a time zone, because `ts` is stored as logged. A relative
    `time_from` such as 'last 7d' ends just after the last Log Entry of the Datasets in scope
    (`corpus_end`, their last timestamps; the latest one when there are several) and leaves
    `time_to` empty. The end is the last timestamp cut to the second plus one second, so the range
    is exclusive at the end and still holds the last Log Entry.
    """
    if time_to is not None and _LOOKS_RELATIVE_RE.match(time_to.strip().lower()):
        raise ScopeError(
            f"time_to={time_to!r} cannot be relative. Put the relative range in time_from, for "
            "example time_from='last 7d', and leave time_to empty."
        )
    if time_from is not None and _LOOKS_RELATIVE_RE.match(time_from.strip().lower()):
        if time_to is not None:
            raise ScopeError(
                f"time_from={time_from!r} is relative and already ends at the Dataset's last Log "
                "Entry, so leave time_to empty, or give both time_from and time_to as ISO-8601 "
                f"timestamps such as {_ISO_EXAMPLES}."
            )
        return _relative(time_from.strip(), datasets, corpus_end)
    start = _absolute("time_from", time_from)
    end = _absolute("time_to", time_to)
    if start is not None and end is not None and start >= end:
        raise ScopeError(f"time_from={time_from!r} must be before time_to={time_to!r}; time_to is exclusive.")
    return TimeRange(start, end, None, None)


def _absolute(name: str, value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ScopeError(
            f"{name}={value!r} is neither an ISO-8601 timestamp (for example {_ISO_EXAMPLES}) nor a "
            "relative range such as 'last 7d' or 'last 24h'. Relative ranges end at the Dataset's "
            "last Log Entry, not today."
        ) from None
    if parsed.tzinfo is not None:
        raise ScopeError(
            f"{name}={value!r} has a time zone, but ts is stored as logged, without a time zone. "
            "Pass the timestamp without the offset, for example '2005-07-01T00:00:00'."
        )
    return parsed


def _relative(value: str, datasets: Sequence[str], corpus_end: Mapping[str, datetime] | None) -> TimeRange:
    match = _RELATIVE_RE.match(value.lower())
    count = int(match[1]) if match and match[1] else 1
    unit = _UNITS.get(match[2]) if match else None
    if unit is None or count < 1:
        raise ScopeError(
            f"time_from={value!r} is not a relative range this server understands. {_RELATIVE_HELP}"
        )
    names = list(datasets) or list(corpus_end or {})
    ends = {name: corpus_end[name] for name in names if corpus_end and name in corpus_end}
    if not ends or len(ends) != len(names):
        missing = [name for name in names if name not in ends] or ["the Datasets in scope"]
        raise ToolContractError(
            f"resolving {value!r} needs corpus_end, the last timestamp of {', '.join(missing)}; the "
            "tool must pass it (from describe_corpus or the corpus registry)"
        )
    latest = max(ends, key=lambda name: ends[name])
    last = ends[latest]
    end = last.replace(microsecond=0) + timedelta(seconds=1)
    against = f"{latest} corpus end {last.isoformat()}"
    if len(ends) > 1:
        against += f" (the latest of {', '.join(names)})"
    return TimeRange(end - count * unit, end, value, against)


# The Data Scope -------------------------------------------------------------------------------------


def build_scope(
    datasets: str | Sequence[str],
    *,
    time_from: str | None = None,
    time_to: str | None = None,
    corpus_end: Mapping[str, datetime] | None = None,
    known_datasets: Iterable[str] | None = None,
    pattern_ids: Sequence[str] | None = None,
    family_id: str | None = None,
    **filters: Any,
) -> DataScope:
    """The Data Scope of one tool call.

    `datasets` names the Datasets (checked against `known_datasets` when given). The time inputs
    go through `resolve_time`; `corpus_end` is needed only for a relative `time_from`. The shared
    filters `pattern_ids` and `family_id` are validated. `filters` are the filters the tool actually
    applied, after validation (for example `query`, `components` or `levels`); the raw call inputs
    are recorded separately in `Evidence.arguments`, so do not pass them here a second time. None,
    empty strings and empty lists are left out, because they filter nothing.
    """
    names = list(dict.fromkeys([datasets] if isinstance(datasets, str) else datasets))
    if not names:
        raise ScopeError(
            "Name at least one dataset, for example dataset='bgl'; call describe_corpus for the Datasets."
        )
    if known_datasets is not None:
        names = validate_choices("dataset", names, known_datasets) or names
    time = resolve_time(time_from, time_to, datasets=names, corpus_end=corpus_end)
    given = {key: value for key, value in filters.items() if value not in (None, "", [], ())}
    return DataScope(
        datasets=names,
        time_from=None if time.start is None else time.start.isoformat(),
        time_to=None if time.end is None else time.end.isoformat(),
        time_input=time.time_input,
        resolved_against=time.resolved_against,
        filters=ScopeFilters.of(
            pattern_ids=validate_pattern_ids(pattern_ids),
            family_id=validate_family_id(family_id),
            **given,
        ),
    )


def time_bounds(scope: DataScope) -> tuple[datetime | None, datetime | None]:
    """The scope's time range as datetimes to bind: start inclusive, end exclusive, None if open."""
    return (
        None if scope.time_from is None else datetime.fromisoformat(scope.time_from),
        None if scope.time_to is None else datetime.fromisoformat(scope.time_to),
    )
