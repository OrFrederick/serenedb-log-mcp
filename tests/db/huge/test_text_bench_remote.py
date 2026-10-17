"""text_bench on S3 itself (T20), marker `remote`: skipped unless SLM_REMOTE_TESTS=1.

These read the public text_bench file from the SereneDB server, so they depend on S3 and the
server's network; `make test-remote` runs them. They check what the design relies on: one file of
1,000,000,000 rows sorted by Timestamp, and a view whose time slice reads only a few row groups.
"""

from __future__ import annotations

import re

import psycopg
import pytest
from psycopg import sql

from serenedb_log_mcp.config import get_settings

pytestmark = [pytest.mark.db, pytest.mark.remote]


def rows(query: sql.Composable) -> list[tuple[object, ...]]:
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(query).fetchall()]


def test_part_000_is_one_billion_rows_sorted_by_timestamp() -> None:
    url = sql.Literal(get_settings().text_bench_url)
    ((count, groups),) = rows(
        sql.SQL("SELECT num_rows, num_row_groups FROM parquet_file_metadata({})").format(url)
    )
    ((unsorted,),) = rows(
        sql.SQL(
            "SELECT count(*) FROM (SELECT CAST(stats_min_value AS TIMESTAMPTZ) AS lo, "
            "lag(CAST(stats_max_value AS TIMESTAMPTZ)) OVER (ORDER BY row_group_id) AS prev_hi "
            "FROM parquet_metadata({}) WHERE lower(path_in_schema) = 'timestamp') AS g WHERE lo < prev_hi"
        ).format(url)
    )

    assert count == 1_000_000_000
    assert groups == 1624
    assert unsorted == 0


def test_a_one_second_slice_through_the_view_reads_a_few_row_groups() -> None:
    url = sql.Literal(get_settings().text_bench_url)
    plan = rows(
        sql.SQL(
            'EXPLAIN ANALYZE SELECT count(*) FROM (SELECT CAST("Timestamp" AS TIMESTAMP) AS ts '
            "FROM read_parquet({})) AS v WHERE ts >= TIMESTAMP '2025-09-23 00:05:00' "
            "AND ts < TIMESTAMP '2025-09-23 00:05:01'"
        ).format(url)
    )
    text = "\n".join(str(row[-1]) for row in plan)

    match = re.search(r"Row Groups Scanned:\s*(\d+)\s*/\s*(\d+)", text)
    assert match is not None, text
    assert int(match.group(1)) <= 4
    assert int(match.group(2)) == 1624
