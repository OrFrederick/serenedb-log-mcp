"""The Pattern catalogue tools (T14) without a database: row shaping, paging by Component, the SQL
templates and the descriptions the agent reads."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import anyio
import pytest
from mcp import Client

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.db import QueryFailed
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.tools._catalogue import (
    COLUMNS,
    ELLIPSIS,
    ROW_BUDGET_CHARS,
    TEXT_CHARS,
    catalogue_row,
    catalogue_statement,
    component_index,
    missing_catalogue,
    paginate,
    row_chars,
    search_statement,
    shape_text,
)

SETTINGS = load_settings({}, slm_schema="slm")
BGL = relation("bgl", settings=SETTINGS)
OS = relation("openstack", settings=SETTINGS)
CATALOGUE_TOOLS = ("list_patterns", "search_patterns")


def row(component: str | None, n: int, text: str = "x", template_id: str | None = None) -> list[Any]:
    return [template_id or f"{n:016x}", component, "INFO", n, "2005-06-03T15:42:50", "2005-06-04T00:00:00",
            None, text]  # fmt: skip


# Rows ------------------------------------------------------------------------------------------------


def test_a_row_has_the_catalogue_columns_with_times_cut_to_the_second() -> None:
    raw = ("a0a0a0a0a0a0a001", "KERNEL", "INFO", 12, datetime(2005, 6, 3, 15, 42, 50, 363779),
           datetime(2005, 7, 1, 0, 0, 0, 1), None, "generating <*>")  # fmt: skip

    assert catalogue_row(raw) == [
        "a0a0a0a0a0a0a001", "KERNEL", "INFO", 12, "2005-06-03T15:42:50", "2005-07-01T00:00:00", None,
        "generating <*>",
    ]  # fmt: skip
    assert COLUMNS == (
        "template_id", "component", "level", "entries",
        "first_seen", "last_seen", "family_id", "template_text",
    )  # fmt: skip


def test_a_search_row_keeps_its_score_after_the_catalogue_columns() -> None:
    raw = ("a0a0a0a0a0a0a001", "KERNEL", "INFO", 12, None, None, "f" * 32, "t", 1.25)

    assert catalogue_row(raw)[8:] == [1.25]


def test_a_short_template_text_is_kept_whole() -> None:
    text = "x" * TEXT_CHARS

    assert shape_text(text) == text


def test_a_long_template_text_keeps_its_start_and_end() -> None:
    text = "While reading FanModule caught " + "client not in a usable state because " * 20 + "Broken pipe"

    shaped = shape_text(text)

    assert shaped is not None
    assert len(shaped) == TEXT_CHARS
    assert shaped.startswith("While reading FanModule caught")
    assert shaped.endswith("Broken pipe")
    assert shaped.count(ELLIPSIS) == 1


# Paging ----------------------------------------------------------------------------------------------


def chars(rows: list[list[Any]]) -> int:
    return sum(row_chars(r) for r in rows)


def test_a_catalogue_within_the_budget_is_one_page() -> None:
    rows = [row("A", 3), row("A", 2), row("B", 1)]

    pages = paginate(rows, budget=10_000)

    assert len(pages) == 1
    assert [list(r) for r in pages[0].rows] == rows
    assert pages[0].components == ["A", "B"]


def test_whole_components_share_a_page_while_they_fit() -> None:
    a, b, c = [row("A", 1)] * 3, [row("B", 2)] * 3, [row("C", 3)] * 3
    budget = chars(a) + chars(b)

    pages = paginate([*a, *b, *c], budget=budget)

    assert [p.components for p in pages] == [["A", "B"], ["C"]]


def test_a_component_larger_than_the_budget_gets_pages_of_its_own() -> None:
    small, big, tail = [row("APP", 1)], [row("KERNEL", n) for n in range(10, 0, -1)], [row("MMCS", 1)]
    budget = chars(big[:4])

    pages = paginate([*small, *big, *tail], budget=budget)

    assert [p.components for p in pages] == [["APP"], ["KERNEL"], ["KERNEL"], ["KERNEL"], ["MMCS"]]
    kernel = [r for p in pages if p.components == ["KERNEL"] for r in p.rows]
    assert [r[3] for r in kernel] == list(range(10, 0, -1)), "largest first, in the given order"


def test_every_row_is_on_exactly_one_page_in_order_and_pages_stay_in_budget() -> None:
    rows = [row(f"C{i % 7}", 100 - i, "w" * (i % 50)) for i in range(300)]
    rows.sort(key=lambda r: (r[1], -r[3]))

    pages = paginate(rows, budget=2_000)

    assert [list(r) for p in pages for r in p.rows] == rows
    assert [p.number for p in pages] == list(range(1, len(pages) + 1))
    assert all(chars([list(r) for r in p.rows]) <= 2_000 for p in pages)


def test_a_row_larger_than_the_budget_still_gets_a_page() -> None:
    pages = paginate([row("A", 1, "x" * 500)], budget=100)

    assert len(pages) == 1
    assert len(pages[0].rows) == 1


def test_an_empty_catalogue_is_one_empty_page() -> None:
    pages = paginate([])

    assert len(pages) == 1
    assert pages[0].rows == ()


def test_rows_without_a_component_are_grouped_under_a_label() -> None:
    assert paginate([row(None, 1)])[0].components == ["(no component)"]


def test_the_component_index_names_the_pages_of_each_component() -> None:
    big = [row("KERNEL", n) for n in range(6, 0, -1)]
    pages = paginate([row("APP", 7), *big], budget=chars(big[:3]))

    index = component_index(pages)

    assert index == {
        "APP": {"patterns": 1, "entries": 7, "pages": [1]},
        "KERNEL": {"patterns": 6, "entries": 21, "pages": [2, 3]},
    }


def test_the_default_page_budget_leaves_room_for_the_envelope() -> None:
    # About 8k tokens per result at 2 characters per token, minus 1,500 tokens for the envelope.
    assert ROW_BUDGET_CHARS == 13_000


# SQL -------------------------------------------------------------------------------------------------


def test_the_catalogue_statement_reads_the_catalogue_table_of_one_dataset() -> None:
    statement = catalogue_statement(BGL)

    assert 'FROM "slm"."pattern" WHERE dataset = %s' in statement.text
    assert statement.params == ("bgl",)
    assert "ORDER BY component NULLS LAST, entries DESC, template_id" in statement.text
    # A Pattern Family of one is the Pattern itself, so family_id is shown only for larger ones.
    assert "count(*) OVER (PARTITION BY family_id)" in statement.text
    assert "CASE WHEN family_patterns > 1 THEN family_id END AS family_id" in statement.text
    assert "WHEN template_source = 'empty' THEN '(no message)'" in statement.text


def test_the_catalogue_filters_are_bound_after_the_dataset() -> None:
    statement = catalogue_statement(OS, component="nova.compute.manager", pattern_ids=["a" * 16],
                                    family_id="f" * 32)  # fmt: skip

    assert "WHERE component = %s AND template_id = ANY(%s::VARCHAR[]) AND family_id = %s" in statement.text
    assert statement.params == ("openstack", "nova.compute.manager", ["a" * 16], "f" * 32)


def test_the_search_statement_scores_words_in_full_and_synonyms_at_half_weight() -> None:
    statement = search_statement(BGL, ["memory", "errors"], ["ddr", "edram"])

    text = statement.text
    assert 'FROM "slm"."pattern_idx" AS p' in text
    assert "BM25(p.tableoid) AS score" in text
    assert 'p."dataset" @@ ts_any(%s::VARCHAR[])::score(NULL)' in text
    assert '(p."template_text" @@ %s OR p."template_text" @@ (%s::TSQUERY ^ 0.5))' in text
    assert statement.params == (["bgl"], "memory errors", "ddr edram", "bgl")


def test_the_search_statement_without_synonyms_has_one_text_predicate() -> None:
    statement = search_statement(OS, ["spawn"])

    assert "^" not in statement.text
    assert statement.params == (["openstack"], "spawn", "openstack")


def test_no_catalogue_statement_sorts_by_the_score() -> None:
    # The tool sorts the matches itself; a BM25 sort with a tie-break crashes SereneDB 26.10.1 (#2).
    for statement in (search_statement(BGL, ["a"], ["b"]), catalogue_statement(BGL)):
        for clause in re.findall(r"ORDER BY ([^\n]+)", statement.text):
            assert "score" not in clause.lower()
            assert "bm25" not in clause.lower()


def test_a_search_without_words_is_a_bug() -> None:
    with pytest.raises(ValueError, match="at least one word"):
        search_statement(BGL, [])


def test_a_missing_catalogue_gets_an_actionable_error() -> None:
    found = missing_catalogue(QueryFailed("Table with name pattern_idx does not exist", "42P01"), BGL)

    assert found is not None
    assert "make derive" in str(found)
    assert missing_catalogue(QueryFailed("syntax error", "42601"), BGL) is None


# Descriptions ----------------------------------------------------------------------------------------


def descriptions() -> dict[str, str]:
    async def listing() -> dict[str, str]:
        async with Client(create_server(), mode="legacy") as client:
            return {
                tool.name: " ".join((tool.description or "").split())
                for tool in (await client.list_tools()).tools
            }

    return anyio.run(listing)


def test_list_patterns_says_to_read_the_catalogue_first_and_to_state_an_unknown() -> None:
    text = descriptions()["list_patterns"]

    assert "Read the catalogue first" in text
    assert "choose none and state that as an Unknown" in text
    for name in ("pattern_ids", "count_by", "search_logs"):
        assert name in text


@pytest.mark.parametrize("tool", CATALOGUE_TOOLS)
def test_the_descriptions_say_when_to_use_which_catalogue_tool(tool: str) -> None:
    text = descriptions()[tool]

    assert "list_patterns to choose Patterns for a concept" in text
    assert "search_patterns when the catalogue is too long to read" in text
    assert "top_patterns" in text
    assert "rank Patterns by frequency within a Data Scope" in text


def test_search_patterns_names_its_method_and_that_no_match_is_not_an_error() -> None:
    text = descriptions()["search_patterns"]

    for word in ("BM25", "stemming", "synonyms"):
        assert word in text
    assert "No match returns a hint in notes, not an error" in text
    assert "Unknown" in text


@pytest.mark.parametrize("tool", CATALOGUE_TOOLS)
def test_the_description_names_inputs_outputs_and_the_columns(tool: str) -> None:
    text = descriptions()[tool]

    assert "Inputs:" in text
    assert "Outputs:" in text
    for column in COLUMNS:
        assert column in text, column
