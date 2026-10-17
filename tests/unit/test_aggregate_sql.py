"""The aggregation SQL templates: placeholders line up with parameters, and inputs are only bound.

`Q` keeps every parameter next to its `%s` in textual order; a template that formats a part in a
different order than its parameters would bind a timestamp where a list belongs. These checks run
every builder with every filter on, without a database.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import pytest

from serenedb_log_mcp.baseline import window
from serenedb_log_mcp.config import Role
from serenedb_log_mcp.corpus import agent_indexes
from serenedb_log_mcp.corpus.agent_indexes import AgentIndex
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.corpus.facts import DatasetFacts
from serenedb_log_mcp.tools import _aggregate
from serenedb_log_mcp.tools._aggregate import (
    EntryFilters,
    Part,
    Prepared,
    Q,
    prepare,
    rank_statement,
    stats_statement,
)
from serenedb_log_mcp.tools.trend import bucket_statement, family_statement

INJECTION = "x'); DROP TABLE t; --"
FILTERS = EntryFilters(
    query=INJECTION,
    components=("KERNEL",),
    levels=("FATAL",),
    sources=("R00-M0-N0-C:J02-U01",),
    alert_categories=("KERNDTLB",),
    pattern_ids=("a0a0a0a0a0a0a001",),
    family_id="f0f0f0f0f0f0f0f0",
)
FIRST, LAST = datetime(2005, 6, 3, 15, 42, 50), datetime(2006, 1, 4, 8, 0, 5)


def part(group_by: str | None, *, previous: bool = True) -> Part:
    start = datetime(2005, 11, 8) if previous else None
    end = datetime(2005, 11, 15) if previous else None
    return Part(relation("bgl"), window("bgl", FIRST, LAST, start, end), group_by, FILTERS)


def statements(group_by: str | None) -> list[Q]:
    p = part(group_by)
    keys = {"bgl": ["KERNEL", "(other)"]}
    return [
        rank_statement([p, part(group_by, previous=False)], 25, show_single_hidden=True),
        stats_statement([p, part(group_by, previous=False)], keys, {"bgl": ["KERNEL"]}),
        bucket_statement(p, ["KERNEL"], "hour"),
        family_statement(p, ["a0a0a0a0a0a0a001"]),
    ]


@pytest.mark.parametrize("group_by", [None, "dataset", "team", "pattern", "alert_category"])
def test_every_placeholder_has_its_parameter(group_by: str | None) -> None:
    for statement in statements(group_by):
        assert statement.text.count("%s") == len(statement.params), statement.text


def test_parameters_follow_the_placeholders_in_order() -> None:
    rank = rank_statement([part("component")], 7, show_single_hidden=False)

    # The query text comes first (full-text predicate), the two `top` values last.
    assert rank.params[0] == INJECTION
    assert rank.params[-2:] == (7, 7)


def test_user_input_is_never_formatted_into_the_sql() -> None:
    for statement in statements("team"):
        assert "DROP TABLE" not in statement.text
        assert "KERNDTLB" not in statement.text
        assert "f0f0f0f0f0f0f0f0" not in statement.text


def test_a_full_text_query_reads_the_index_and_no_statement_sorts_by_bm25() -> None:
    for statement in statements("component")[:3]:
        assert '"bgl_entry_idx"' in statement.text
    for statement in statements(None):
        assert "BM25" not in statement.text.upper()


def test_without_a_query_the_base_table_is_read() -> None:
    plain = Part(relation("bgl"), window("bgl", FIRST, LAST), "component", EntryFilters())

    text = rank_statement([plain], 5, show_single_hidden=True).text
    assert '"bgl_entry_idx"' not in text
    assert '"bgl_entry"' in text


# ----------------------------------------------------------------- agent index routing (T19)

WAVE = AgentIndex(
    schema="slm",
    name="agent_idx_bgl_0a1b2c3d",
    dataset="bgl",
    time_from=datetime(2005, 8, 22),
    time_to=datetime(2005, 9, 19),
    rows=310_868,
)


@pytest.fixture
def prepared_inside_wave(monkeypatch: pytest.MonkeyPatch) -> Iterator[Prepared]:
    """A count_by call by component with a query, prepared for a Data Scope inside WAVE's slice."""
    known = DatasetFacts("bgl", 4_747_963, FIRST, LAST, {"KERNEL": 1}, {"FATAL": 1}, {})
    monkeypatch.setattr(_aggregate, "dataset_facts", lambda name, role: known)
    agent_indexes.clear()
    agent_indexes.register(WAVE)
    yield prepare(
        Role.ENGINEER,
        ["bgl"],
        group_by="component",
        query="lustre mount failed",
        time_from="2005-08-29",
        time_to="2005-09-05",
        components=None,
        levels=None,
        sources=None,
        alert_categories=None,
        pattern_ids=None,
        family_id=None,
    )
    agent_indexes.clear()


def test_inside_an_agent_index_slice_the_scoped_statements_read_the_agent_index(
    prepared_inside_wave: Prepared,
) -> None:
    rank = rank_statement(prepared_inside_wave.parts, 5, show_single_hidden=True)
    bucket = bucket_statement(prepared_inside_wave.parts[0], ["KERNEL"], "day")

    for statement in (rank, bucket):
        assert '"agent_idx_bgl_0a1b2c3d"' in statement.text
        assert '"bgl_entry_idx"' not in statement.text


def test_the_baselines_read_the_full_index_because_they_reach_outside_the_slice(
    prepared_inside_wave: Prepared,
) -> None:
    # The median week and the previous period count matches over the whole Dataset; the agent
    # index holds only its slice, so reading it would turn every week outside the slice into 0.
    stats = stats_statement(prepared_inside_wave.parts, {"bgl": ["KERNEL"]}, {"bgl": ["KERNEL"]})

    assert '"bgl_entry_idx"' in stats.text
    assert '"agent_idx_bgl_0a1b2c3d"' not in stats.text
