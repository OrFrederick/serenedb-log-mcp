"""The aggregation SQL templates: placeholders line up with parameters, and inputs are only bound.

`Q` keeps every parameter next to its `%s` in textual order; a template that formats a part in a
different order than its parameters would bind a timestamp where a list belongs. These checks run
every builder with every filter on, without a database.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from serenedb_log_mcp.baseline import window
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.tools._aggregate import EntryFilters, Part, Q, rank_statement, stats_statement
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
