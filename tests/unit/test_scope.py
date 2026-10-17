"""`scope.py`: enum validation with hints, corpus-relative time and the shared Pattern filters."""

from __future__ import annotations

from datetime import datetime

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp.evidence import DataScope
from serenedb_log_mcp.scope import (
    ScopeError,
    TimeRange,
    build_scope,
    resolve_time,
    time_bounds,
    validate_choice,
    validate_choices,
    validate_family_id,
    validate_pattern_ids,
)

COMPONENTS = ["KERNEL", "APP", "MMCS", "LINKCARD", "DISCOVERY", "MONITOR", "HARDWARE", "BGLMASTER"]
BGL_END = datetime(2006, 1, 4, 8, 0, 5, 233639)
OS_END = datetime(2017, 5, 16, 23, 59, 59, 120000)
ENDS = {"bgl": BGL_END, "openstack": OS_END}
PATTERN_A = "3f2a9c0d1b4e5f67"
PATTERN_B = "00ff00ff00ff00ff"


# Enum validation --------------------------------------------------------------------------------------


def test_a_known_value_passes_unchanged() -> None:
    assert validate_choice("component", "KERNEL", COMPONENTS) == "KERNEL"


def test_a_value_in_another_case_resolves_to_the_canonical_value() -> None:
    assert validate_choice("component", "kernel", COMPONENTS) == "KERNEL"


def test_a_misspelt_value_suggests_the_closest_value() -> None:
    with pytest.raises(ScopeError) as caught:
        validate_choice("component", "KERNAL", COMPONENTS)

    message = str(caught.value)
    assert isinstance(caught.value, ToolError)
    assert "component='KERNAL'" in message
    assert "Did you mean 'KERNEL'?" in message


def test_a_value_with_no_close_match_lists_the_valid_values() -> None:
    with pytest.raises(ScopeError) as caught:
        validate_choice("component", "zzz", COMPONENTS)

    message = str(caught.value)
    assert "Did you mean" not in message
    assert "'KERNEL'" in message
    assert "describe_corpus" in message


def test_a_long_list_of_valid_values_is_cut_short() -> None:
    allowed = [f"node-{i:03d}" for i in range(100)]

    with pytest.raises(ScopeError) as caught:
        validate_choice("node", "zzz", allowed)

    assert "and 80 more" in str(caught.value)


def test_a_value_that_matches_two_values_in_another_case_is_ambiguous() -> None:
    with pytest.raises(ScopeError, match="'Info'"):
        validate_choice("level", "info", ["INFO", "Info"])


def test_no_values_means_no_filter() -> None:
    assert validate_choices("level", None, ["INFO"]) is None
    assert validate_choices("level", [], ["INFO"]) is None


def test_values_are_canonical_and_deduplicated_in_order() -> None:
    assert validate_choices("component", ["kernel", "APP", "KERNEL"], COMPONENTS) == ["KERNEL", "APP"]


def test_every_unknown_value_is_reported_in_one_error() -> None:
    with pytest.raises(ScopeError) as caught:
        validate_choices("component", ["KERNAL", "APP", "MONITR"], COMPONENTS)

    message = str(caught.value)
    assert "'KERNEL'" in message
    assert "'MONITOR'" in message


# Shared Pattern filters -----------------------------------------------------------------------------


def test_pattern_ids_accept_16_hex_template_ids() -> None:
    assert validate_pattern_ids([PATTERN_A, PATTERN_B.upper(), PATTERN_A]) == [PATTERN_A, PATTERN_B]


def test_no_pattern_ids_means_no_filter() -> None:
    assert validate_pattern_ids(None) is None
    assert validate_pattern_ids([]) is None


@pytest.mark.parametrize(
    "bad", ["E218", "KERNDTLB", "3f2a9c0d1b4e5f6", "3f2a9c0d1b4e5f678", "3f2a9c0d1b4e5fzz"]
)
def test_anything_but_a_16_hex_template_id_is_rejected_with_a_hint(bad: str) -> None:
    with pytest.raises(ScopeError) as caught:
        validate_pattern_ids([PATTERN_A, bad])

    message = str(caught.value)
    assert repr(bad) in message
    assert "16" in message
    assert "list_patterns" in message


@pytest.mark.parametrize("family", ["0123456789abcdef", "0123456789abcdef0123456789abcdef"])
def test_family_id_accepts_hex_ids(family: str) -> None:
    assert validate_family_id(family.upper()) == family


def test_no_family_id_means_no_filter() -> None:
    assert validate_family_id(None) is None


def test_a_malformed_family_id_is_rejected_with_a_hint() -> None:
    with pytest.raises(ScopeError, match="list_patterns"):
        validate_family_id("EDRAM")


# Time -----------------------------------------------------------------------------------------------


def test_no_time_means_the_whole_dataset() -> None:
    assert resolve_time(None, None) == TimeRange(None, None, None, None)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2005-07-01", datetime(2005, 7, 1)),
        ("2005-07-01T12:30:00", datetime(2005, 7, 1, 12, 30)),
        ("2005-07-01 12:30:00.250000", datetime(2005, 7, 1, 12, 30, 0, 250000)),
    ],
)
def test_absolute_times_are_parsed_as_iso_8601(given: str, expected: datetime) -> None:
    resolved = resolve_time(given, None)

    assert resolved == TimeRange(expected, None, None, None)


def test_time_from_must_be_before_time_to() -> None:
    with pytest.raises(ScopeError, match="before"):
        resolve_time("2005-07-02", "2005-07-01")


def test_a_time_zone_is_rejected_because_ts_is_stored_as_logged() -> None:
    with pytest.raises(ScopeError, match="time zone"):
        resolve_time("2005-07-01T00:00:00Z", None)


def test_an_unreadable_time_explains_both_forms() -> None:
    with pytest.raises(ScopeError) as caught:
        resolve_time("yesterday", None)

    message = str(caught.value)
    assert "time_from='yesterday'" in message
    assert "2005-07-01" in message
    assert "last 7d" in message


def test_last_7d_resolves_against_the_datasets_last_timestamp() -> None:
    resolved = resolve_time("last 7d", None, datasets=["bgl"], corpus_end=ENDS)

    assert resolved.end == datetime(2006, 1, 4, 8, 0, 6)  # the last Log Entry is inside
    assert resolved.start == datetime(2005, 12, 28, 8, 0, 6)
    assert resolved.time_input == "last 7d"
    assert resolved.resolved_against == "bgl corpus end 2006-01-04T08:00:05.233639"


@pytest.mark.parametrize(
    ("given", "hours"),
    [
        ("last 24h", 24),
        ("last 2w", 14 * 24),
        ("last 30m", 0.5),
        ("last week", 7 * 24),
        ("last hour", 1),
        ("last 3 days", 72),
        ("past 7d", 7 * 24),
        ("  LAST 7D ", 7 * 24),
    ],
)
def test_relative_forms(given: str, hours: float) -> None:
    resolved = resolve_time(given, None, datasets=["bgl"], corpus_end=ENDS)

    assert resolved.start is not None
    assert resolved.end is not None
    assert (resolved.end - resolved.start).total_seconds() == hours * 3600
    assert resolved.time_input == given.strip()


@pytest.mark.parametrize("given", ["last 0d", "last month", "last 7y"])
def test_unsupported_relative_forms_are_rejected(given: str) -> None:
    with pytest.raises(ScopeError, match="last 30d"):
        resolve_time(given, None, datasets=["bgl"], corpus_end=ENDS)


def test_a_relative_time_from_cannot_have_a_time_to() -> None:
    with pytest.raises(ScopeError, match="time_to"):
        resolve_time("last 7d", "2006-01-01", datasets=["bgl"], corpus_end=ENDS)


def test_time_to_cannot_be_relative() -> None:
    with pytest.raises(ScopeError, match="time_from"):
        resolve_time(None, "last 7d", datasets=["bgl"], corpus_end=ENDS)


def test_a_relative_time_needs_the_corpus_end_from_the_tool() -> None:
    with pytest.raises(ValueError, match="corpus_end"):
        resolve_time("last 7d", None, datasets=["bgl"])


def test_several_datasets_resolve_against_the_latest_end() -> None:
    resolved = resolve_time("last 1d", None, datasets=["bgl", "openstack"], corpus_end=ENDS)

    assert resolved.end == datetime(2017, 5, 17, 0, 0, 0)
    assert resolved.resolved_against == (
        "openstack corpus end 2017-05-16T23:59:59.120000 (the latest of bgl, openstack)"
    )


# The Data Scope -------------------------------------------------------------------------------------


def test_build_scope_fills_the_data_scope() -> None:
    scope = build_scope(
        ["bgl"],
        time_from="2005-07-01",
        time_to="2005-08-01",
        pattern_ids=[PATTERN_A],
        family_id="0123456789ABCDEF",
        components=["KERNEL"],
        query="ciod failed",
        levels=None,
    )

    assert isinstance(scope, DataScope)
    assert scope.datasets == ["bgl"]
    assert scope.time_from == "2005-07-01T00:00:00"
    assert scope.time_to == "2005-08-01T00:00:00"
    assert scope.time_input is None
    assert scope.resolved_against is None
    assert scope.filters.pattern_ids == [PATTERN_A]
    assert scope.filters.family_id == "0123456789abcdef"
    assert scope.filters.model_dump(exclude_none=True) == {
        "pattern_ids": [PATTERN_A],
        "family_id": "0123456789abcdef",
        "components": ["KERNEL"],
        "query": "ciod failed",
    }


def test_build_scope_records_the_relative_input_and_the_resolved_range() -> None:
    scope = build_scope(["bgl"], time_from="last 7d", corpus_end=ENDS)

    assert scope.time_input == "last 7d"
    assert scope.resolved_against == "bgl corpus end 2006-01-04T08:00:05.233639"
    assert scope.time_from == "2005-12-28T08:00:06"
    assert scope.time_to == "2006-01-04T08:00:06"


def test_build_scope_validates_the_shared_filters() -> None:
    with pytest.raises(ScopeError, match="list_patterns"):
        build_scope(["bgl"], pattern_ids=["E218"])


def test_build_scope_needs_a_dataset() -> None:
    with pytest.raises(ScopeError, match="dataset"):
        build_scope([])


def test_build_scope_checks_datasets_against_the_known_ones() -> None:
    with pytest.raises(ScopeError, match="Did you mean 'bgl'"):
        build_scope(["bgll"], known_datasets=["bgl", "openstack"])


def test_time_bounds_give_back_the_datetimes_to_bind() -> None:
    scope = build_scope(["bgl"], time_from="last 7d", corpus_end=ENDS)

    assert time_bounds(scope) == (datetime(2005, 12, 28, 8, 0, 6), datetime(2006, 1, 4, 8, 0, 6))
    assert time_bounds(build_scope(["bgl"])) == (None, None)


def test_build_scope_accepts_one_dataset_name() -> None:
    assert build_scope("openstack").datasets == ["openstack"]
