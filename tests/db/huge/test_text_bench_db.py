"""text_bench end to end through MCP on SereneDB (T20), over a synthetic OTel Parquet file.

count_by, trend, create_index and drop_index run unchanged on the remote Dataset; the resolver
enforces the time slice and the agent index for full text. Every count is checked against SQL that
reads the Parquet file directly, not through the view or the index.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg
import pytest
from aggregation_tools import call, call_error, row_dicts
from psycopg import sql

from serenedb_log_mcp.config import get_settings
from serenedb_log_mcp.corpus.datasets import TB_COLUMNS
from serenedb_log_mcp.index_admin import existing_agent_indexes

pytestmark = pytest.mark.db

SLICE = {"time_from": "2025-09-23T00:00:10", "time_to": "2025-09-23T00:00:20"}
PREVIOUS = ("2025-09-23 00:00:00+00", "2025-09-23 00:00:10+00")
INDEXED = {"dataset": "text_bench", "time_from": "2025-09-23T00:00:00", "time_to": "2025-09-23T00:00:30"}


def direct(path: str, query: str, params: list[Any]) -> list[tuple[Any, ...]]:
    """Run `query` with {f} bound to read_parquet(path): the file itself, not the view or an index."""
    statement = sql.SQL(query).format(f=sql.SQL("read_parquet({})").format(sql.Literal(path)))
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(statement, params).fetchall()]


def per_service(path: str, start: str, end: str, words: str | None = None) -> dict[str, int]:
    text = ' AND regexp_matches(lower("Body"), %s)' if words else ""
    params: list[Any] = [start, end, *([words] if words else [])]
    rows = direct(
        path,
        'SELECT "ServiceName", count(*) FROM {f} WHERE "Timestamp" >= CAST(%s AS TIMESTAMPTZ) '
        f'AND "Timestamp" < CAST(%s AS TIMESTAMPTZ){text} GROUP BY 1',
        params,
    )
    return {str(service): int(n) for service, n in rows}


def test_the_setup_registers_the_view_with_canonical_columns_and_its_facts(text_bench: Any) -> None:
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        described = conn.execute(
            sql.SQL("DESCRIBE {}").format(sql.Identifier(text_bench.schema, "tb_entry"))
        ).fetchall()
        facts = conn.execute(
            sql.SQL("SELECT kind, value, n, first, last FROM {} ORDER BY kind, value").format(
                sql.Identifier(text_bench.schema, "tb_facts")
            )
        ).fetchall()

    assert tuple(row[0] for row in described) == TB_COLUMNS
    assert dict(zip((row[0] for row in described), (row[1] for row in described), strict=True))["ts"] == (
        "TIMESTAMP"
    )
    assert ("range", None, 6_000, datetime(2025, 9, 23), datetime(2025, 9, 23, 0, 0, 59, 990000)) in facts
    assert ("component", "frontend", 2_000, None, None) in facts
    assert ("level", "error", 1_200, None, None) in facts


def test_count_by_over_a_slice_matches_the_file_and_has_a_previous_period(text_bench: Any) -> None:
    result = call("count_by", role="team_lead", group_by="component", dataset="text_bench", **SLICE)

    counts = {row["component"]: row["count"] for row in row_dicts(result)}
    previous = {row["component"]: row["prev_period"] for row in row_dicts(result)}
    assert counts == per_service(text_bench.path, "2025-09-23 00:00:10+00", "2025-09-23 00:00:20+00")
    assert previous == per_service(text_bench.path, *PREVIOUS)
    assert result["summary"]["total"] == 1_000
    assert result["data_scope"]["agent_indexes"] is None


def test_an_unbounded_call_is_refused_with_a_slice_and_create_index(text_bench: Any) -> None:
    message = call_error("count_by", role="manager", group_by="level", dataset="text_bench")

    assert "time_from" in message
    assert "60 seconds" in message
    assert "create_index(dataset='text_bench'" in message


def test_a_call_that_names_no_dataset_never_reads_text_bench(text_bench: Any) -> None:
    # The scratch schema holds only text_bench: the Base Corpus tables are missing, and the error
    # names them, so the call went to bgl and openstack, never to the remote view.
    message = call_error("count_by", role="team_lead", group_by="level")

    assert "bgl_entry" in message


def test_full_text_needs_an_agent_index_and_then_reads_it(text_bench: Any) -> None:
    asked = call_error(
        "count_by", role="engineer", group_by="component", dataset="text_bench", query="visa", **SLICE
    )
    assert "create_index(dataset='text_bench'" in asked

    built = call("create_index", role="engineer", **INDEXED)
    assert built["summary"]["status"] == "built"
    assert built["summary"]["rows"] == 3_000
    assert built["summary"]["max_rows"] == 5_000_000
    assert any("snapshot" in note and "REINDEX INDEX" in note for note in built["notes"])
    assert any("REINDEX INDEX" in statement for statement in built["sql"])
    name = built["summary"]["index"]

    found = call(
        "count_by", role="engineer", group_by="component", dataset="text_bench", query="visa", **SLICE
    )
    counts = {row["component"]: row["count"] for row in row_dicts(found)}
    assert counts == per_service(
        text_bench.path, "2025-09-23 00:00:10+00", "2025-09-23 00:00:20+00", r"\bvisa\b"
    )
    assert found["data_scope"]["agent_indexes"] == {"text_bench": f"{text_bench.schema}.{name}"}
    assert any("snapshot" in note for note in found["notes"])
    previous = {row["component"]: row["prev_period"] for row in row_dicts(found)}
    assert previous == per_service(text_bench.path, *PREVIOUS, r"\bvisa\b")

    trend = call("trend", role="engineer", dataset="text_bench", bucket="minute", query="visa", **SLICE)
    assert trend["summary"]["total"] == sum(counts.values())

    assert [index.name for index in existing_agent_indexes()] == [name]
    dropped = call("drop_index", role="engineer", name=name)
    assert dropped["summary"]["status"] == "dropped"
    assert existing_agent_indexes() == []
