"""The pure parts of bench/index_tradeoff.py (T19): timing parse, break-even, SQL and the report."""

from __future__ import annotations

from datetime import datetime

import pytest

import index_tradeoff as bench
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus.datasets import relation

SLM = load_settings({})
START, END = datetime(2005, 8, 22), datetime(2005, 9, 19)


def test_the_server_time_is_read_from_the_explain_analyze_summary() -> None:
    text = "╭─ Summary ───────────╮\n│ Total Time: 0.0016s │\n╰─────────────────────╯\n╭─ UNGROUPED ─╮"

    assert bench.total_time_ms(text) == pytest.approx(1.6)


def test_a_plan_without_a_total_time_is_an_error() -> None:
    with pytest.raises(bench.BenchError, match="Total Time"):
        bench.total_time_ms("no summary here")


@pytest.mark.parametrize(
    ("build_ms", "saved_ms", "expected"),
    [
        (600.0, 10.0, 60),
        (600.0, 7.0, 86),  # rounded up: 85.7 queries do not repay it yet
        (600.0, 0.0, None),  # never
        (600.0, -3.0, None),  # the index is slower: never
    ],
)
def test_break_even_is_the_build_time_over_the_time_saved_per_query(
    build_ms: float, saved_ms: float, expected: int | None
) -> None:
    assert bench.break_even(build_ms, saved_ms) == expected


def test_the_scan_matches_every_word_as_a_whole_word_on_the_base_table() -> None:
    statement = bench.query_sql(
        bench.QUERIES[0], relation("bgl", settings=SLM), "scan", START, END, index=None
    ).as_string(None)

    assert 'FROM "slm"."bgl_entry" AS e' in statement
    for word in bench.QUERIES[0].words:
        assert f"(^|[^a-z0-9]){word}([^a-z0-9]|$)" in statement
    assert "@@" not in statement
    assert "'2005-08-22 00:00:00'::timestamp" in statement


def test_the_index_modes_use_the_full_text_predicate_on_the_given_index() -> None:
    rel = relation("bgl", settings=SLM)

    full = bench.query_sql(bench.QUERIES[0], rel, "full", START, END, index=rel.index).as_string(None)

    assert 'FROM "slm"."bgl_entry_idx" AS e' in full
    assert "websearch_to_tsquery('lustre mount failed')" in full
    assert "regexp_matches" not in full


def test_no_benchmark_query_sorts_by_bm25() -> None:
    rel = relation("bgl", settings=SLM)
    for query in bench.QUERIES:
        for mode in ("scan", "full"):
            text = bench.query_sql(query, rel, mode, START, END, index=rel.index).as_string(None)
            assert "BM25" not in text.upper()


def test_the_report_states_the_slice_the_build_and_both_break_evens() -> None:
    report = bench.Report(
        dataset="bgl",
        time_from=START,
        time_to=END,
        slice_rows=310_868,
        dataset_rows=4_747_963,
        builds=[bench.Build(400.0, 100.0), bench.Build(420.0, 90.0), bench.Build(380.0, 110.0)],
        queries=[
            bench.Timed(
                bench.QUERIES[0],
                27_392,
                {"scan": [9.0, 9.4, 9.8], "full": [2.0, 2.2, 2.4], "agent": [0.5, 0.6, 0.7]},
                {"scan": [99.0], "full": [40.0], "agent": [38.0]},
            ),
        ],
        tools=[
            bench.ToolTiming(
                "count_by", "group_by='node'", [300.0, 310.0, 320.0], [200.0, 210.0, 220.0], True
            )
        ],
        environment={"SereneDB": "26.10.1", "Round trip (SELECT 1), median": "101.0 ms"},
    )

    text = report.markdown()

    assert "310,868" in text
    assert "4,747,963" in text
    assert "500.0 ms" in text  # median build: 400 + 100
    assert "| lustre mount failed | count |" in text
    assert "26.10.1" in text
    # mean saving vs scan 8.8 ms -> 57 queries; vs full 1.6 ms -> 313 queries
    assert "57 queries" in text
    assert "313 queries" in text


def test_the_report_names_where_the_index_pays_most_and_where_it_saves_nothing() -> None:
    def timed(shape: bench.Shape, full: float, agent: float) -> bench.Timed:
        query = bench.Query(("ciod",), shape)
        return bench.Timed(query, 50_444, {"scan": [12.0], "full": [full], "agent": [agent]},
                           {"scan": [90.0], "full": [90.0], "agent": [90.0]})  # fmt: skip

    report = bench.Report(
        dataset="bgl",
        time_from=START,
        time_to=END,
        slice_rows=310_868,
        dataset_rows=4_747_963,
        builds=[bench.Build(400.0, 60.0)],
        queries=[timed("by_source", 64.0, 6.0), timed("count", 1.8, 1.2), timed("first_rows", 12.0, 16.9)],
        tools=[],
    )

    text = report.markdown()

    assert "Pays off fastest:** ciod / by_source (full index 64.0 ms, agent index 6.0 ms: 8 queries)" in text
    assert "No saving against the full index:** ciod / first_rows" in text


def test_the_report_says_build_ms_is_wall_time_and_gives_the_network_free_build() -> None:
    report = bench.Report(
        dataset="bgl",
        time_from=START,
        time_to=END,
        slice_rows=310_868,
        dataset_rows=4_747_963,
        builds=[bench.Build(400.0, 100.0), bench.Build(420.0, 90.0), bench.Build(380.0, 110.0)],
        queries=[
            bench.Timed(
                bench.QUERIES[0],
                27_392,
                {"scan": [9.0, 9.4, 9.8], "full": [2.0, 2.2, 2.4], "agent": [0.5, 0.6, 0.7]},
                {"scan": [99.0], "full": [40.0], "agent": [38.0]},
            ),
        ],
        tools=[],
        server_builds=[300.0, 310.0, 320.0],
        rtt_ms=80.0,
    )

    text = report.markdown()

    assert "client wall time" in text
    assert "one network round trip per statement" in text
    assert "server-side CREATE INDEX (EXPLAIN ANALYZE, median of 3 separate builds): 310.0 ms" in text
    # network-free build: 310 server + max(0, 100 refresh - 80 round trip) = 330 ms
    assert "330.0 ms" in text
    assert "38 queries against a scan and 207 queries against the full index" in text
    assert "compare the modes within one report" in text


def test_without_server_builds_the_report_still_renders() -> None:
    query = bench.Timed(bench.QUERIES[0], 1, {"scan": [2.0], "full": [2.0], "agent": [1.0]},
                        {"scan": [9.0], "full": [9.0], "agent": [9.0]})  # fmt: skip
    report = bench.Report("bgl", START, END, 1, 2, [bench.Build(1.0, 1.0)], [query], [])

    assert "server-side CREATE INDEX" not in report.markdown()
