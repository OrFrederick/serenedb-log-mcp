"""Fixtures for the text_bench db tests (T20): a small OTel-shaped Parquet file instead of S3.

`otel_file` writes OTEL_ROWS synthetic Log Entries with the column names and types of
text_bench/part_000.parquet (60 s at 100 per second, sorted by Timestamp) to a file on the SereneDB
server, with COPY ... TO, and runs `make huge-setup`'s script against it into a scratch schema
`scratch_t20_<hex>`. So these tests exercise the real view, facts, partial index over the view and
REINDEX, without network access. `text_bench` points SLM_SCHEMA at that schema for one test, with
an empty Evidence Ledger and agent index registry, and drops any agent index the test left behind.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import sql

import huge_setup
from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import agent_indexes, facts

OTEL_ROWS = 6_000
"""60 seconds at 100 Log Entries per second from 2025-09-23 00:00:00 UTC."""

# Every 7th Body mentions "visa", every 5th is an error, every 3rd comes from frontend; four Log
# Entries share a trace, and every 11th has no trace (an empty TraceId, as in the real file).
OTEL_SELECT = """
SELECT TIMESTAMPTZ '2025-09-23 00:00:00+00' + i * INTERVAL '10 milliseconds' AS "Timestamp",
       CASE WHEN i % 3 = 0 THEN 'frontend' ELSE 'cart' END AS "ServiceName",
       CASE WHEN i % 5 = 0 THEN 'error' ELSE 'info' END AS "SeverityText",
       'request ' || i || CASE WHEN i % 7 = 0 THEN ' failed to charge card: Visa cache full' ELSE ' ok' END
           AS "Body",
       CASE WHEN i % 11 = 0 THEN '' ELSE md5(CAST(i // 4 AS VARCHAR)) END AS "TraceId",
       left(md5(CAST(i AS VARCHAR)), 16) AS "SpanId"
FROM range(0, {rows}) AS r(i)
ORDER BY 1
"""


@dataclass(frozen=True)
class OtelFile:
    schema: str
    path: str


@pytest.fixture(scope="session")
def otel_file(database_url: str) -> Iterator[OtelFile]:
    schema = f"scratch_t20_{uuid.uuid4().hex[:8]}"
    path = f"/tmp/{schema}.parquet"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("COPY ({}) TO {} (FORMAT PARQUET)").format(
                sql.SQL(OTEL_SELECT.format(rows=OTEL_ROWS)), sql.Literal(path)
            )
        )
    try:
        assert huge_setup.main(["--schema", schema, "--url", path, "--timeout", "120"]) == 0
        yield OtelFile(schema, path)
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def text_bench(otel_file: OtelFile, database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[OtelFile]:
    """The synthetic text_bench as this test's SLM_SCHEMA, with no agent index before or after."""
    monkeypatch.setattr(
        config, "_active", load_settings(slm_schema=otel_file.schema, text_bench_url=otel_file.path)
    )
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    agent_indexes.clear()
    yield otel_file
    agent_indexes.clear()
    facts.clear_cache()
    with psycopg.connect(database_url, autocommit=True) as conn:
        for (name,) in conn.execute(
            "SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relkind = 'i' AND c.relname LIKE 'agent_idx_%%'",
            [otel_file.schema],
        ).fetchall():
            conn.execute(sql.SQL("DROP INDEX IF EXISTS {}").format(sql.Identifier(otel_file.schema, name)))
