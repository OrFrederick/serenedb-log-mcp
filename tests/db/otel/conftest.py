"""Fixtures for the otel_demo db tests (T21): small OTel-shaped Parquet files instead of S3.

`otel_demo_files` writes the five otel_demo files, with the column names and types the views read,
to a fresh directory on the SereneDB server (COPY ... TO; a throwaway partitioned write creates the
directory), and runs `make otel-setup`'s script against them into a scratch schema
`scratch_t21_<hex>`. So these tests exercise the real views, facts, metric roll-up and baselines
without network access. `otel_demo` points SLM_SCHEMA and SLM_OTEL_DEMO_URL at them for one test,
with an empty Evidence Ledger.

The data is one hour, 2025-05-25 00:00 to 01:00 UTC:

- the trace TRACE at 00:30:00, seven spans over five services, shaped like the real investigation:
  load-generator POST > frontend POST > checkout PlaceOrder > (cart GetCart, checkout Charge client >
  payment Charge server > payment "Error: Visa cache full" internal span); its Log Entries are a
  payment info and a payment error (span P1) and a frontend error (span F1);
- 600 background Log Entries (one every 6 s; every 5th an error, every 3rd from frontend, every 7th
  without a trace) and 100 background traces of one cart span each;
- metrics, one or two points per minute: payment CPU at 0.5 in minutes 29 to 31 and 0.02 otherwise,
  checkout CPU flat at 0.05, payment transactions (a cumulative counter) +10 a minute but +1 in minute
  30, payment http.client.duration (a cumulative histogram) with a mean of 20 ms but 100 ms in minute
  30, payment jvm.memory.used flat (a non-monotonic sum), and two cluster metrics without a service.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from otel_demo_data import FILES, OtelDemo
from psycopg import sql

import otel_setup
from serenedb_log_mcp import config, ledger, toolkit
from serenedb_log_mcp.config import OTEL_DEMO_FILES, load_settings
from serenedb_log_mcp.corpus import facts


@pytest.fixture(scope="session")
def otel_demo_files(database_url: str) -> Iterator[OtelDemo]:
    schema = f"scratch_t21_{uuid.uuid4().hex[:8]}"
    directory = f"/tmp/{schema}"
    with psycopg.connect(database_url, autocommit=True) as conn:
        # COPY TO does not create directories, but a partitioned write does.
        conn.execute(
            sql.SQL("COPY (SELECT 1 AS x, 'made' AS p) TO {} (FORMAT PARQUET, PARTITION_BY (p))").format(
                sql.Literal(directory)
            )
        )
        for signal, select in FILES.items():
            target = f"{directory}/{OTEL_DEMO_FILES[signal]}"
            conn.execute(
                sql.SQL("COPY ({}) TO {} (FORMAT PARQUET)").format(sql.SQL(select), sql.Literal(target))
            )
    try:
        assert otel_setup.main(["--schema", schema, "--url", directory, "--timeout", "120"]) == 0
        yield OtelDemo(schema, directory)
    finally:
        with psycopg.connect(database_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def otel_demo(otel_demo_files: OtelDemo, monkeypatch: pytest.MonkeyPatch) -> Iterator[OtelDemo]:
    """The synthetic otel_demo as this test's SLM_SCHEMA, with an empty Evidence Ledger."""
    monkeypatch.setattr(
        config,
        "_active",
        load_settings(slm_schema=otel_demo_files.schema, otel_demo_url=otel_demo_files.directory),
    )
    monkeypatch.setattr(ledger, "_active", None)
    monkeypatch.setattr(toolkit, "_role_hook", toolkit.identity_role_hook)
    facts.clear_cache()
    yield otel_demo_files
    facts.clear_cache()
