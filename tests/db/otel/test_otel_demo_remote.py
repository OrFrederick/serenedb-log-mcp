"""otel_demo on S3 itself (T21), marker `remote`: skipped unless SLM_REMOTE_TESTS=1 (`make test-remote`).

These read the public otel_demo files from the SereneDB server, so they depend on S3 and the server's
network, and a cold run takes minutes. They check what the design relies on (row counts, TraceId bloom
filters, no time order, so slices are pointless) and the worked investigation's trace in
docs/results/multimodal.md, through get_trace over views in a scratch schema (views only: the metric
roll-up is left out, so the run stays short).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from aggregation_tools import call, row_dicts
from psycopg import sql

import otel_setup
from derive import render, split_statements
from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import get_settings, load_settings

pytestmark = [pytest.mark.db, pytest.mark.remote]

TRACE = "b60c4584ee2689aaa9751261d1b9d800"
"""The trace of the worked investigation: a payment "Visa cache full" error during checkout."""


def rows(query: sql.Composable) -> list[tuple[Any, ...]]:
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        return [tuple(row) for row in conn.execute(query).fetchall()]


def url(signal: str) -> sql.Literal:
    return sql.Literal(load_settings().otel_demo_files()[signal])


def test_the_files_hold_the_documented_rows() -> None:
    ((logs,),) = rows(sql.SQL("SELECT num_rows FROM parquet_file_metadata({})").format(url("logs")))
    ((spans,),) = rows(sql.SQL("SELECT num_rows FROM parquet_file_metadata({})").format(url("traces")))

    assert logs == 91_559_064
    assert spans == 216_701_061


@pytest.mark.parametrize("signal", ["logs", "traces"])
def test_every_row_group_has_a_trace_id_bloom_filter(signal: str) -> None:
    ((groups, with_bloom),) = rows(
        sql.SQL(
            "SELECT count(*), count(bloom_filter_offset) FROM parquet_metadata({}) "
            "WHERE path_in_schema = 'TraceId'"
        ).format(url(signal))
    )

    assert groups > 100
    assert with_bloom == groups


def test_the_log_file_is_not_sorted_by_time_so_a_slice_would_prune_nothing() -> None:
    ((overlapping, groups),) = rows(
        sql.SQL(
            "SELECT count(*) FILTER (WHERE lo < prev_hi), count(*) FROM ("
            "SELECT CAST(stats_min_value AS TIMESTAMPTZ) AS lo, "
            "lag(CAST(stats_max_value AS TIMESTAMPTZ)) OVER (ORDER BY row_group_id) AS prev_hi "
            "FROM parquet_metadata({}) WHERE path_in_schema = 'TimestampTime') AS g"
        ).format(url("logs"))
    )

    assert overlapping > groups // 2


def test_a_trace_lookup_reads_only_the_row_groups_that_hold_the_trace() -> None:
    plan = rows(
        sql.SQL('EXPLAIN ANALYZE SELECT count(*) FROM read_parquet({}) WHERE "TraceId" = {}').format(
            url("traces"), sql.Literal(TRACE)
        )
    )
    text = "\n".join(str(row[-1]) for row in plan)

    match = re.search(r"Row Groups Scanned:\s*(\d+)\s*/\s*(\d+)", text)
    assert match is not None, text
    assert int(match.group(2)) == 354
    assert 1 <= int(match.group(1)) <= 30


@pytest.fixture
def views(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The otel_demo views (no facts, no roll-up) in a scratch schema, as this test's SLM_SCHEMA."""
    schema = f"scratch_t21_remote_{uuid.uuid4().hex[:8]}"
    settings = load_settings(slm_schema=schema, trace_timeout_s=600.0)
    script = render(
        otel_setup.SETUP_SQL.read_text(), otel_setup.variables(schema, settings.otel_demo_files())
    )
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for statement in split_statements(script):
            if statement.startswith(("DROP VIEW", "CREATE VIEW")):
                conn.execute(statement)
    monkeypatch.setattr(config, "_active", settings)
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    try:
        yield schema
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def test_the_investigated_trace_ends_in_the_payment_cache_error(views: str) -> None:
    result = call("get_trace", role="engineer", trace_id=TRACE)

    summary = result["summary"]
    assert summary["spans_total"] == 29
    assert summary["error_spans"] == 8
    assert [(origin["service"], origin["operation"]) for origin in summary["error_origin_spans"]] == [
        ("payment", "Error: Visa cache full: cannot add new item.")
    ]
    assert summary["log_entries_total"] == 13
    assert summary["error_log_entries"] == 5
    logs = [row for row in row_dicts(result) if row["signal"] == "log"]
    assert any(
        row["service"] == "payment" and row["text"] == "Visa cache full: cannot add new item." for row in logs
    )
    assert any("make otel-setup" in note for note in result["notes"])  # the roll-up is not in this schema
