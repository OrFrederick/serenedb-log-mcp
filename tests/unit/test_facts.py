"""`corpus/facts.py`: one statement per Dataset for its time range and filter values, cached."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import pytest

from serenedb_log_mcp.config import Role, load_settings, set_settings
from serenedb_log_mcp.corpus import facts
from serenedb_log_mcp.db import QueryFailed, QueryResult
from serenedb_log_mcp.scope import ScopeError

FIRST = datetime(2005, 6, 3, 15, 42, 50, 363779)
LAST = datetime(2006, 1, 4, 8, 0, 5, 233639)
ROWS: list[tuple[Any, ...]] = [
    ("range", None, 4_747_963, FIRST, LAST),
    ("component", "KERNEL", 4_324_967, None, None),
    ("component", "APP", 228_536, None, None),
    ("level", "INFO", 3_735_823, None, None),
    ("level", "FATAL", 855_501, None, None),
    ("alert_category", "KERNDTLB", 152_734, None, None),
]


class FakeFetch:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, Role | None]] = []

    def __call__(self, sql: str, params: Sequence[Any] = (), *, role: Role | None = None) -> QueryResult:
        self.calls.append((sql, role))
        return QueryResult(len(self.rows), self.rows, False, 1.0, [sql], [list(params)])


@pytest.fixture(autouse=True)
def empty_cache() -> None:
    facts.clear_cache()


def test_the_facts_hold_the_range_and_the_filter_values_with_counts() -> None:
    found = facts.dataset_facts("bgl", Role.TEAM_LEAD, fetch=FakeFetch(ROWS))

    assert found.dataset == "bgl"
    assert found.entries == 4_747_963
    assert (found.first, found.last) == (FIRST, LAST)
    assert found.components == {"KERNEL": 4_324_967, "APP": 228_536}
    assert found.levels == {"INFO": 3_735_823, "FATAL": 855_501}
    assert found.alert_categories == {"KERNDTLB": 152_734}


def test_one_statement_reads_the_resolved_table_and_only_bgl_asks_for_alert_categories() -> None:
    set_settings(load_settings({}, slm_schema="scratch_x"))
    bgl, openstack = FakeFetch(ROWS), FakeFetch(ROWS[:4])

    facts.dataset_facts("bgl", None, fetch=bgl)
    facts.dataset_facts("openstack", None, fetch=openstack)

    assert len(bgl.calls) == len(openstack.calls) == 1
    assert '"scratch_x"."bgl_entry"' in bgl.calls[0][0]
    assert "alert_tag" in bgl.calls[0][0]
    assert '"scratch_x"."os_entry"' in openstack.calls[0][0]
    assert "alert_tag" not in openstack.calls[0][0]


def test_the_facts_are_cached_per_schema_and_dataset() -> None:
    fetch = FakeFetch(ROWS)

    first = facts.dataset_facts("bgl", Role.ENGINEER, fetch=fetch)
    again = facts.dataset_facts("bgl", Role.MANAGER, fetch=fetch)
    set_settings(load_settings({}, slm_schema="other"))
    facts.dataset_facts("bgl", Role.ENGINEER, fetch=fetch)

    assert again is first
    assert len(fetch.calls) == 2


def test_the_role_reaches_the_database_layer() -> None:
    fetch = FakeFetch(ROWS)

    facts.dataset_facts("bgl", Role.MANAGER, fetch=fetch)

    assert fetch.calls[0][1] is Role.MANAGER


def test_an_empty_dataset_has_no_time_range() -> None:
    found = facts.dataset_facts("openstack", None, fetch=FakeFetch([("range", None, 0, None, None)]))

    assert found.entries == 0
    assert found.first is None
    assert found.last is None
    assert found.components == {}


def test_a_null_component_or_level_is_left_out_of_the_filter_values() -> None:
    rows = [*ROWS, ("component", None, 3, None, None)]

    found = facts.dataset_facts("bgl", None, fetch=FakeFetch(rows))

    assert None not in found.components


def test_a_remote_dataset_reads_its_precomputed_facts_instead_of_scanning() -> None:
    # Computing the facts of text_bench per call would scan a billion remote rows; `make huge-setup`
    # stores them once in tb_facts, in the same (kind, value, n, first, last) shape.
    first, last = datetime(2025, 9, 23), datetime(2025, 9, 23, 0, 29, 24, 39037)
    fetch = FakeFetch([("range", None, 1_000_000_000, first, last), ("component", "frontend", 9, None, None)])

    found = facts.dataset_facts("text_bench", fetch=fetch)

    statement = fetch.calls[0][0]
    assert 'FROM "slm"."tb_facts"' in statement
    assert "tb_entry" not in statement
    assert found.entries == 1_000_000_000
    assert (found.first, found.last) == (first, last)
    assert found.components == {"frontend": 9}


def test_a_remote_dataset_that_was_never_set_up_says_how_to_set_it_up() -> None:
    def missing(sql: str, params: Sequence[Any] = (), *, role: Role | None = None) -> QueryResult:
        raise QueryFailed('SereneDB rejected the query (42P01): relation "tb_facts" does not exist', "42P01")

    with pytest.raises(ScopeError) as excinfo:
        facts.dataset_facts("text_bench", fetch=missing)

    assert "make huge-setup" in str(excinfo.value)
