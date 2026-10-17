"""`search_logs` SQL: one WHERE for the count, the rows and the facets; score-neutral filters; and
never a BM25 sort with a second key (#2 round 2: a tie-break key after the score crashes SereneDB
26.10.1 in 28 of 70 runs)."""

from __future__ import annotations

import itertools
import re
from datetime import datetime
from pathlib import Path

import pytest

from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import relation
from serenedb_log_mcp.db import QueryResult
from serenedb_log_mcp.fts import QUERY_MODES
from serenedb_log_mcp.tools.search_logs import SORTS, Search, read_facets

SETTINGS = load_settings({}, slm_schema="slm")
BGL = relation("bgl", settings=SETTINGS)
OS = relation("openstack", settings=SETTINGS)
START, END = datetime(2005, 7, 1), datetime(2005, 8, 1)

SRC_DIR = Path(__file__).parents[2] / "src"
ORDER_BY = re.compile(r"ORDER\s+BY\s+(.+?)(?=\s+LIMIT\b|[\"')]|$)", re.IGNORECASE | re.MULTILINE)
SCORE_ALIAS = re.compile(r"\bBM25\([^)]*\)\s+AS\s+(\w+)", re.IGNORECASE)


def bm25_sorts(text: str) -> list[str]:
    """Every ORDER BY clause of `text` that sorts by a BM25 score, directly or through its alias.

    `ORDER BY score DESC, line_no` with `BM25(...) AS score` is the shape that crashed (#2).
    """
    aliases = {alias.lower() for alias in SCORE_ALIAS.findall(text)}
    # Hide the scorer's own parentheses, so the clause does not end at "BM25(i.tableoid".
    masked = re.sub(r"\bBM25\(([^)]*)\)", r"BM25⟨\1⟩", text, flags=re.IGNORECASE)
    found = []
    for masked_clause in ORDER_BY.findall(masked):
        clause = masked_clause.replace("⟨", "(").replace("⟩", ")")
        words = {word.lower() for word in re.findall(r"\w+", clause)}
        if "bm25" in words or words & aliases:
            found.append(clause.strip())
    return found


def full() -> Search:
    return Search(
        BGL,
        text="ciod failed",
        start=START,
        end=END,
        components=("APP",),
        levels=("FATAL",),
        sources=("R00-M0-N0-C:J02-U01",),
        alert_categories=("APPSEV",),
        pattern_ids=("a0a0a0a0a0a0a001",),
        family_id="f0f0f0f0f0f0f0f0",
    )


def test_the_count_reads_the_index_with_the_text_predicate_and_score_neutral_filters() -> None:
    statement = full().count_statement()

    assert statement.text == (
        'SELECT count(*) FROM "slm"."bgl_entry_idx" AS e WHERE '
        'e."message" @@ websearch_to_tsquery(%s)'
        ' AND e."ts" @@ ts_ge(%s)::score(NULL)'
        ' AND e."ts" @@ ts_lt(%s)::score(NULL)'
        ' AND e."component" @@ ts_any(%s::VARCHAR[])::score(NULL)'
        ' AND e."level" @@ ts_any(%s::VARCHAR[])::score(NULL)'
        ' AND e."node" @@ ts_any(%s::VARCHAR[])::score(NULL)'
        ' AND e."alert_tag" @@ ts_any(%s::VARCHAR[])::score(NULL)'
        ' AND e."template_id" @@ ts_any(%s::VARCHAR[])::score(NULL)'
        ' AND e."family_id" @@ ts_any(%s::VARCHAR[])::score(NULL)'
    )
    assert statement.params == (
        "ciod failed",
        START,
        END,
        ["APP"],
        ["FATAL"],
        ["R00-M0-N0-C:J02-U01"],
        ["APPSEV"],
        ["a0a0a0a0a0a0a001"],
        ["f0f0f0f0f0f0f0f0"],
    )


def test_the_rows_and_the_facets_bind_the_same_filters_as_the_count() -> None:
    search = full()
    where = search.where()

    for statement in (search.rows_statement(), search.facets_statement()):
        assert where.text in statement.text
        assert statement.params == where.params


def test_time_order_is_oldest_first_on_the_utc_epoch_with_the_entry_id_as_tie_break() -> None:
    text = full().rows_statement().text

    assert text.endswith('ORDER BY e."epoch_utc", e."entry_id" LIMIT %s')


def test_newest_order_is_the_reverse() -> None:
    text = Search(BGL, text="error", sort="newest").rows_statement().text

    assert text.endswith('ORDER BY e."epoch_utc" DESC, e."entry_id" DESC LIMIT %s')


def test_relevance_sorts_by_the_bm25_score_alone_and_returns_it() -> None:
    text = Search(BGL, text="error", sort="relevance").rows_statement().text

    assert "BM25(e.tableoid) AS score" in text
    assert text.endswith("ORDER BY BM25(e.tableoid) DESC LIMIT %s")


def test_the_rows_carry_the_highlighted_message_when_there_is_a_text_query() -> None:
    text = Search(BGL, text="error").rows_statement().text

    assert "ts_highlight(e.\"message\", 'StartSel=**, StopSel=**, HighlightAll=true') AS snippet" in text


def test_a_pattern_only_search_has_no_text_predicate_and_no_highlight() -> None:
    search = Search(BGL, pattern_ids=("a0a0a0a0a0a0a001",))

    assert search.where().text == 'e."template_id" @@ ts_any(%s::VARCHAR[])::score(NULL)'
    assert "ts_highlight" not in search.rows_statement().text


def test_the_columns_follow_the_dataset_and_the_sort() -> None:
    assert Search(BGL, text="x").columns == [
        "entry_id",
        "ts",
        "node",
        "component",
        "level",
        "alert_tag",
        "template_id",
        "message",
    ]
    assert Search(OS, text="x", sort="relevance").columns == [
        "entry_id",
        "ts",
        "component",
        "level",
        "request_id",
        "instance_ref",
        "template_id",
        "message",
        "score",
    ]


def test_openstack_sources_are_instances_and_it_has_no_racks() -> None:
    search = Search(OS, text="spawn", sources=("u1",))

    assert 'e."instance_ref" @@ ts_any' in search.where().text
    facets = search.facets_statement().text
    assert 'e."instance_ref" AS src' in facets
    assert "CAST(NULL AS VARCHAR) AS rack" in facets
    assert '"slm"."os_entry_idx" AS e' in facets


def test_the_facets_count_sources_racks_the_time_range_components_and_levels() -> None:
    text = full().facets_statement().text

    assert "count(DISTINCT src)" in text
    assert "count(DISTINCT rack)" in text
    assert "min(ts)" in text
    assert "max(ts)" in text
    assert "GROUP BY component" in text
    assert "GROUP BY level" in text


@pytest.mark.parametrize(("mode", "sort"), list(itertools.product(QUERY_MODES, SORTS)))
def test_no_search_statement_sorts_by_bm25_plus_another_key(mode: str, sort: str) -> None:
    search = Search(
        BGL,
        text="ciod failed",
        mode=mode,  # type: ignore[arg-type]
        sort=sort,  # type: ignore[arg-type]
        start=START,
        components=("APP",),
    )

    statements = (search.count_statement(), search.rows_statement(), search.facets_statement())
    sorts = [clause for statement in statements for clause in bm25_sorts(statement.text)]
    assert sorts == (["BM25(e.tableoid) DESC"] if sort == "relevance" else [])


def test_the_guard_catches_a_tie_break_after_the_score_or_its_alias() -> None:
    assert bm25_sorts("SELECT x FROM i WHERE m @@ 'a' ORDER BY BM25(i.tableoid) DESC, x LIMIT 5") == [
        "BM25(i.tableoid) DESC, x"
    ]
    assert bm25_sorts("SELECT BM25(i.tableoid) AS score FROM i ORDER BY score DESC, line_no LIMIT 20") == [
        "score DESC, line_no"
    ]
    assert bm25_sorts("SELECT x FROM t ORDER BY ts, entry_id LIMIT 5") == []


def test_no_sql_in_the_source_tree_sorts_by_bm25_plus_another_key() -> None:
    offenders = [
        f"{path.relative_to(SRC_DIR)}: ORDER BY {clause}"
        for path in SRC_DIR.rglob("*.py")
        for clause in bm25_sorts(path.read_text())
        if "," in clause
    ]
    assert not offenders


def facet_rows(racks: int) -> QueryResult:
    rows: list[tuple[object, ...]] = [
        ("all", None, 5, 1, racks, START, END),
        ("component", "MMCS", 3, None, None, None, None),
        ("component", "KERNEL", 2, None, None, None, None),
        ("level", "INFO", 5, None, None, None, None),
    ]
    return QueryResult(len(rows), rows, False, 1.0, ["facets"], [[]])


def test_bgl_matches_without_a_rack_have_zero_racks_and_openstack_has_none() -> None:
    bgl = read_facets(facet_rows(0), 5, has_racks=True)
    openstack = read_facets(facet_rows(0), 5, has_racks=False)

    assert bgl["distinct_racks"] == 0
    assert openstack["distinct_racks"] is None
    assert bgl["top_components"] == {"MMCS": 3, "KERNEL": 2}
    assert bgl["components_total"] == 2
    assert (bgl["first_seen"], bgl["last_seen"]) == (START.isoformat(), END.isoformat())


def test_facets_that_disagree_with_the_count_are_an_error() -> None:
    with pytest.raises(RuntimeError, match="changed between them"):
        read_facets(facet_rows(1), 6, has_racks=True)
