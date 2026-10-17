"""`top_patterns` without a database (T28): the SQL binds every input, the flag rules, the drift
hand-off and the description."""

from __future__ import annotations

from datetime import datetime

import anyio
import pytest
from mcp import Client

from serenedb_log_mcp.baseline import window
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools._aggregate import EntryFilters, Part
from serenedb_log_mcp.tools.top_patterns import (
    DAY_S,
    NEW_MIN_ENTRIES,
    SORT_ORDER,
    CatalogueEntry,
    catalogue_statement,
    handoffs,
    is_chronic,
    is_new,
    measure_statement,
    plan,
)

INJECTION = "x'); DROP TABLE t; --"
FILTERS = EntryFilters(
    query=INJECTION,
    components=("KERNEL",),
    levels=("FATAL",),
    sources=("R00-M0-N0-C:J02-U01",),
    pattern_ids=("a0a0a0a0a0a0a001",),
    family_id="f0f0f0f0f0f0f0f0",
)
FIRST, LAST = datetime(2005, 6, 3, 15, 42, 50), datetime(2006, 1, 4, 8, 0, 5)
OS_FIRST, OS_LAST = datetime(2017, 5, 14, 19, 39, 1), datetime(2017, 5, 16, 0, 0, 0)


def part(dataset: str = "bgl") -> Part:
    first, last = (FIRST, LAST) if dataset == "bgl" else (OS_FIRST, OS_LAST)
    return Part(relation(dataset), window(dataset, first, last), None, FILTERS)


@pytest.mark.parametrize("sort", sorted(SORT_ORDER))
@pytest.mark.parametrize("dataset", ["bgl", "openstack"])
@pytest.mark.parametrize("candidates_only", [False, True])
def test_every_placeholder_has_its_parameter(sort: str, dataset: str, candidates_only: bool) -> None:
    statement = measure_statement(plan(part(dataset), candidates_only), sort, 20)

    assert statement.text.count("%s") == len(statement.params), statement.text
    assert INJECTION not in statement.text
    assert statement.params[-1] == 20


def test_the_catalogue_statement_binds_its_ids() -> None:
    statement = catalogue_statement(part(), [INJECTION])

    assert statement.text.count("%s") == len(statement.params) == 2
    assert INJECTION not in statement.text


def test_the_priority_candidates_are_alert_lines_and_request_failures() -> None:
    bgl = measure_statement(plan(part("bgl"), True), "priority", 5).text
    openstack = measure_statement(plan(part("openstack"), True), "priority", 5)

    assert '"alert_tag" IS NOT NULL' in bgl
    assert '"http_status" >= 500' in openstack.text
    assert ["ERROR", "CRITICAL"] in openstack.params


def test_the_whole_bgl_plan_has_a_28_day_trend_and_a_7_day_new_window() -> None:
    p = plan(part(), True)

    assert p.trend_days == 28
    assert p.new_days == 7
    assert p.weeks == 31
    # epoch_utc of BGL: ts is US Pacific; 2005-06-03 15:42:50 PDT is 22:42:50 UTC.
    assert p.start_ep == datetime.fromisoformat("2005-06-03T22:42:50+00:00").timestamp()


def test_a_short_scope_shrinks_the_windows() -> None:
    p = plan(part("openstack"), True)

    assert p.trend_days == pytest.approx(p.span_days)
    assert p.new_days == pytest.approx(p.span_days / 4)


@pytest.mark.parametrize(
    ("weeks", "active", "peak", "entries", "expected"),
    [
        (31, 31, 30, 291, True),
        (31, 15, 10, 100, False),  # not more than half of the weeks
        (31, 20, 60, 100, False),  # one week holds more than half: a burst, not a steady rate
        (3, 3, 1, 3, False),  # too short to tell
    ],
)
def test_chronic_rule(weeks: int, active: int, peak: int, entries: int, expected: bool) -> None:
    assert is_chronic(weeks=weeks, active_weeks=active, peak_week=peak, entries=entries) is expected


def test_new_rule() -> None:
    end = 1_000_000.0

    assert is_new(first_ever_ep=end - 6 * DAY_S, end_ep=end, new_days=7, entries=NEW_MIN_ENTRIES)
    assert not is_new(first_ever_ep=end - 8 * DAY_S, end_ep=end, new_days=7, entries=100)
    assert not is_new(first_ever_ep=end - DAY_S, end_ep=end, new_days=7, entries=NEW_MIN_ENTRIES - 1)


T0 = 1_132_084_800.0
"""2005-11-15 20:00 UTC."""
END = T0 + 50 * DAY_S


def days(n: float) -> float:
    return n * DAY_S


def test_handoff_to_the_family_pattern_that_starts_as_this_one_ends() -> None:
    old = CatalogueEntry("a", T0 - days(150), T0, (("new", T0 + days(0.125), END),))
    new = CatalogueEntry("b", T0 + days(0.125), END, (("old", T0 - days(150), T0),))

    assert handoffs(old) == ("new", None)
    assert handoffs(new) == (None, "old")


def test_handoff_when_the_new_wording_starts_before_the_old_one_ends() -> None:
    # BGL 2005-11-15: the "over N seconds" wording starts six days before the old one stops.
    old = CatalogueEntry("a", T0 - days(160), T0 + days(6), (("new", T0, END),))

    assert handoffs(old) == ("new", None)


def test_no_handoff_when_the_partner_is_far_away_in_time() -> None:
    lone = CatalogueEntry("a", T0 - days(150), T0, (("far", T0 + days(30), END),))

    assert handoffs(lone) == (None, None)


def description() -> str:
    async def listing() -> str:
        async with Client(create_server(), mode="legacy") as client:
            tools = {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}
            return tools["top_patterns"]

    return " ".join(anyio.run(listing).split())


def test_the_description_names_when_to_rank_and_when_to_choose() -> None:
    text = description()

    assert "Use top_patterns to rank" in text
    assert "list_patterns to choose Patterns for a concept" in text
    assert "never round" in text
    assert "Outputs:" in text


def test_the_catalogue_is_read_on_the_utc_clock_with_its_display_text() -> None:
    text = catalogue_statement(part(), ["a0a0a0a0a0a0a001"]).text

    assert "p.display_text" in text
    assert "f.first_epoch" in text
    assert "first_seen" not in text
