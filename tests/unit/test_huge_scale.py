"""bench/huge_scale.py (T20): the pure parts, without a database or the network."""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import datetime
from typing import Any

import pytest

import huge_scale
from huge_scale import (
    Build,
    Measured,
    Plan,
    Report,
    ToolRun,
    TradeOff,
    content_length,
    file_urls,
    fmt_bytes,
    parse_plan,
    parse_size,
    text_query,
)
from index_tradeoff import BenchError

# The plan SereneDB 26.10.1 printed for the whole-file GROUP BY over part_000 (abridged boxes).
WHOLE_FILE_PLAN = """┌─────────────────────────────────────┐
│┌───────────────────────────────────┐│
││         HTTPFS HTTP Stats         ││
││                                   ││
││           in: 511.9 MiB           ││
││            out: 0 bytes           ││
││             #HEAD: 1              ││
││             #GET: 3229            ││
│└───────────────────────────────────┘│
└─────────────────────────────────────┘
╭─ Summary ───────────╮
│ Total Time:  75.10s │
│ Data Read: 536.7 MB │
╰─────────────────────╯
╭─ HASH_GROUP_BY ─────────────────────────────────╮
│ 52 rows                                  10.38s │
╰─────────────────────┒┎──────────────────────────╯
╭─ TABLE_SCAN ────────┚┖──────────────────────────╮
│ Function: READ_PARQUET                          │
│ Row Groups Scanned: 1624 / 1624                 │
│ 1,000,000,000 rows                        3.90s │
╰─────────────────────────────────────────────────╯"""

INDEX_PLAN = """╭─ Summary ──────────╮
│ Total Time: 0.0075s │
╰────────────────────╯
╭─ UNGROUPED_AGGREGATE ─╮
│ 1 row             0µs │
╰───────────────────────╯"""


def test_sizes_parse_in_binary_and_decimal_units() -> None:
    assert parse_size("1.1 MiB") == round(1.1 * 1024**2)
    assert parse_size("536.7 MB") == 536_700_000
    assert parse_size("0 bytes") == 0
    with pytest.raises(BenchError):
        parse_size("lots")


def test_a_remote_plan_yields_rows_row_groups_bytes_and_time() -> None:
    plan = parse_plan(WHOLE_FILE_PLAN)

    assert plan.total_ms == pytest.approx(75_100)
    assert plan.rows_scanned == 1_000_000_000
    assert plan.row_groups == (1624, 1624)
    assert plan.http_in == round(511.9 * 1024**2)
    assert plan.http_gets == 3229
    assert plan.data_read == 536_700_000
    assert plan.cold


def test_a_plan_without_a_remote_read_has_no_bytes() -> None:
    plan = parse_plan(INDEX_PLAN)

    assert plan.total_ms == pytest.approx(7.5)
    assert plan.http_in is None
    assert plan.rows_scanned is None
    assert not plan.cold


def test_a_plan_without_a_total_time_is_an_error() -> None:
    with pytest.raises(BenchError, match="Total Time"):
        parse_plan("nothing here")


def test_the_file_list_follows_the_brief() -> None:
    urls = file_urls("https://h/text_bench/part_000.parquet", 52)

    assert urls[0] == "https://h/text_bench/part_000.parquet"
    assert urls[49] == "https://h/text_bench/part_049.parquet"
    assert len(urls) == 52
    assert file_urls("/data/one.parquet", 5) == ["/data/one.parquet"]


class FakeResponse(io.BytesIO):
    def __init__(self, length: str | None) -> None:
        super().__init__()
        self.headers = {} if length is None else {"Content-Length": length}


def test_content_length_comes_from_a_head_request() -> None:
    asked: list[Any] = []

    def opener(request: Any, timeout: float) -> FakeResponse:
        asked.append(request)
        return FakeResponse("40991166725")

    assert content_length("https://h/part_000.parquet", opener) == 40_991_166_725
    assert asked[0].get_method() == "HEAD"


def test_a_missing_file_has_no_size_and_a_local_path_is_not_asked() -> None:
    def missing(request: Any, timeout: float) -> FakeResponse:
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    assert content_length("https://h/part_099.parquet", missing) is None
    assert content_length("/tmp/x.parquet", missing) is None


def test_bytes_read_in_decimal_with_binary_next_to_them() -> None:
    assert fmt_bytes(40_991_166_725) == "40.99 GB (38.18 GiB)"
    assert fmt_bytes(0) == "0 B"
    assert fmt_bytes(None) == "n/a"


def test_the_scan_and_the_index_ask_the_same_question() -> None:
    start, end = datetime(2025, 9, 23, 0, 16), datetime(2025, 9, 23, 0, 16, 8)
    scan = text_query('"huge"."tb_entry"', "scan", "visa", "count", start, end)
    index = text_query('"huge"."agent_idx_text_bench_0a1b2c3d"', "index", "visa", "count", start, end)

    assert "regexp_matches(e.message" in scan
    assert "websearch_to_tsquery('visa')" in index
    for statement in (scan, index):
        assert (
            "e.ts >= '2025-09-23 00:16:00'::timestamp AND e.ts < '2025-09-23 00:16:08'::timestamp"
            in statement
        )


def measured(
    label: str, *ms: float, http_in: int | None = None, rows: int | None = None, groups: int = 8
) -> Measured:
    plans = [Plan(m, rows, (groups, 1624), http_in if i == 0 else 0) for i, m in enumerate(ms)]
    return Measured(label, "SELECT 1", plans)


def report() -> Report:
    return Report(
        url="https://h/text_bench/part_000.parquet",
        env={"Server threads": "12", "SereneDB": "26.10.1"},
        sizes=[(f"https://h/text_bench/part_{i:03d}.parquet", 40_000_000_000) for i in range(50)]
        + [("https://h/text_bench/part_050.parquet", None)],
        layout={
            "rows": 1_000_000_000,
            "row_groups": 1624,
            "bytes": 40_991_166_725,
            "first": datetime(2025, 9, 23),
            "last": datetime(2025, 9, 23, 0, 29, 24),
            "seconds": 1764.0,
            "rows_per_second": 566_893,
            "sorted": "yes",
            "footer_ms": 320.0,
            "created_by": "ClickHouse",
        },
        columns=[("Body", 9_074_835_467, 87_206_695_395)],
        setup_s=None,
        in_place=[
            measured("Whole file: per Component", 75_100, http_in=536_766_054, rows=122_846_925, groups=1624),
            measured("60 s slice: count", 5_420, http_in=36_390_000, rows=33_000_000, groups=55),
        ],
        tools=[ToolRun("count_by", "group_by='component'", [900.0, 800.0, 850.0], [1.0], "total 9", False)],
        guard="text_bench is about 1,000,000,000 OTel Log Entries ... create_index ...",
        cold_scans=measured("cold", 7_500, 7_000, 8_000, http_in=50_000_000, rows=4_900_000),
        builds=[Build(16_000, 15_600, 400, 4_767_635), Build(15_000, 14_700, 300, 4_767_635)],
        tradeoffs=[
            TradeOff("count", "visa", measured("w", 350, 340, 330), measured("a", 7, 6, 6), 352_102, 352_000)
        ],
        routed_tool=ToolRun("count_by", "query='visa'", [300.0], [1.0], "total 5", True),
        patterns={
            "ms": 7_000.0,
            "entries": 4_767_635,
            "distinct": 900_000,
            "keys": 4_000,
            "top": [("a <*> b", 9, 2)],
        },
        runs=3,
    )


def test_the_first_build_is_cold_and_the_later_ones_cached() -> None:
    r = report()

    assert r.build_cold.build_ms == 16_000
    assert r.build_warm.build_ms == 15_000


def test_three_break_evens_cover_a_fresh_slice_a_lost_cache_and_a_cached_slice() -> None:
    r = report()

    assert r.cold_ms == 7_500
    # Fresh slice: 16,000 + 6n <= 7,500 + 340 (n - 1)  ->  n >= 26.47
    assert r.break_even_fresh() == 27
    assert huge_scale.fresh_break_even(16_000, 7_500, 340, 6) == 27
    # Every scan cold: 16,000 / (7,500 - 6), rounded up.
    assert r.break_even_cold() == 3
    # Cached slice: the cached build 15,000 / (340 - 6), rounded up.
    assert r.break_even_warm() == 45


def test_an_index_that_saves_nothing_never_pays_off_and_a_cheap_one_pays_off_at_once() -> None:
    assert huge_scale.fresh_break_even(16_000, 7_500, 300, 300) is None
    assert huge_scale.fresh_break_even(1_000, 7_500, 300, 6) == 1


def test_the_report_states_rows_bytes_times_cost_and_environment() -> None:
    text = report().markdown()

    for heading in (
        "## Result",
        "## Data size (HTTP HEAD)",
        "## Layout of part_000",
        "## Queries in place, without an index",
        "## Through the tools",
        "## Agent index trade-off",
        "## Pattern keys of one slice (g10)",
        "## Cost",
        "## Environment",
        "## Method and limits",
    ):
        assert heading in text
    assert "1,000,000,000" in text
    assert "all 1,000,000,000 rows" in text  # the whole-file scan covered every row group
    assert "Files found: 50" in text
    assert "Not found: part_050.parquet" in text
    assert "536.77 MB" in text
    assert "never money" in text
    assert "$" not in text
    assert "€" not in text
    assert "nan" not in text
    assert "Extrapolation, labelled as such" in text


@pytest.mark.parametrize("runs", ["0", "4"])
def test_runs_must_fit_the_fresh_cold_slices(runs: str) -> None:
    # One fresh slice per cold run, so every cold run fetches its bytes from S3.
    with pytest.raises(SystemExit):
        huge_scale.main(["--runs", runs])


def test_rows_covered_are_exact_for_the_whole_file_and_proportional_for_a_slice() -> None:
    whole = Plan(44_000, 122_846_925, (1624, 1624), 1)
    part = Plan(300, 33_808_384, (55, 1624), 0)

    assert huge_scale.rows_covered(whole, 1_000_000_000) == "1,000,000,000"
    assert huge_scale.rows_covered(part, 1_000_000_000) == "≈33,866,995"
    assert huge_scale.rows_covered(Plan(7.5), 1_000_000_000) == "n/a"


def test_a_saved_run_renders_the_same_report_again() -> None:
    original = report()
    raw = json.loads(json.dumps(huge_scale.to_json(original), default=str))

    assert huge_scale.from_json(raw).markdown() == original.markdown()
