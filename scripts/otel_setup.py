#!/usr/bin/env python3
"""Register the remote Dataset otel_demo: logs, traces and metrics of one OTel demo run (T21).

Usage (see `make help`):
    uv run python scripts/otel_setup.py [--schema S] [--url DIR] [--timeout SECONDS] [--no-warm]

Runs sql/remote/otel_demo.sql inside SLM_SCHEMA (default slm), which creates:
    otel_log               view over the logs file (the Dataset's Log Entries)
    otel_span              view over the traces file
    otel_metric_gauge      views over the gauge, sum and histogram files
    otel_metric_sum
    otel_metric_histogram
    otel_facts             time range and row count (Parquet footer), Components and levels (one pass
                           over two narrow columns)
    otel_metric_minute     per-minute roll-up of every metric a service reports (about 3 million rows)
    otel_metric_baseline   per series: p05, median and p95 of its per-minute values

DIR is SLM_OTEL_DEMO_URL, by default the public otel_demo directory on S3. The SereneDB server reads
the files itself, so it needs outbound HTTPS; nothing here downloads them. On the shared dev server
(12 threads) the whole run took about 85 s, nearly all of it the metric roll-up, which reads about
600 MB of metric columns once.

Then it warms the TraceId bloom filters of the log and trace files (about 425 MiB, a minute on the
shared server) with one lookup each, so the first get_trace call does not pay for them; SereneDB
keeps them in its file cache until it restarts. --no-warm skips that.

It never indexes anything and never copies a log, span or metric row: the views read the files in
place. It re-runs safely.

Environment: DATABASE_URL, SLM_SCHEMA, SLM_OTEL_DEMO_URL.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg import sql

from derive import SQL_DIR, DeriveError
from huge_setup import CONNECT_TIMEOUT_S, DEFAULT_TIMEOUT_S, is_utc, run_file
from serenedb_log_mcp.config import ConfigError, load_settings
from serenedb_log_mcp.corpus.datasets import DATASETS, ENTRY_TABLES, FACTS_TABLES, SIGNAL_TABLES

SETUP_SQL = SQL_DIR / "remote" / "otel_demo.sql"
WARM_TRACE_ID = "7fffffffffffffffffffffffffffffff"
"""A trace id inside the TraceId range of every row group (ids are random hex), so min/max statistics
prune nothing and the lookup must read every bloom filter. It matches no trace."""


def variables(schema: str, files: Mapping[str, str]) -> dict[str, str]:
    """Placeholder values: the quoted schema and each signal's file as a SQL string literal."""
    return {"slm": sql.Identifier(schema).as_string(None)} | {
        signal: sql.Literal(url).as_string(None) for signal, url in files.items()
    }


def warm_statements(schema: str) -> list[tuple[str, str]]:
    """One TraceId lookup per file that has bloom filters: (statement, what it warms)."""
    spans = sql.Identifier(schema, SIGNAL_TABLES["otel_demo"]["spans"]).as_string(None)
    logs = sql.Identifier(schema, ENTRY_TABLES["otel_demo"]).as_string(None)
    probe = sql.Literal(WARM_TRACE_ID).as_string(None)
    return [
        (
            f"SELECT count(*) FROM {spans} WHERE trace_id = {probe}",
            "TraceId bloom filters of the traces file",
        ),
        (f"SELECT count(*) FROM {logs} WHERE trace_id = {probe}", "TraceId bloom filters of the logs file"),
    ]


def warm(conn: psycopg.Connection[Any], schema: str, timeout_s: float) -> None:
    """Run the warm-up lookups, each cancelled after `timeout_s`; a failure only prints a warning."""
    for statement, what in warm_statements(schema):
        timer = threading.Timer(timeout_s, conn.cancel_safe)
        timer.daemon = True
        started = time.perf_counter()
        timer.start()
        try:
            conn.execute(statement)
        except psycopg.Error as exc:
            print(f"warning: warming the {what} failed ({exc}); get_trace still works, only slower once")
            continue
        finally:
            timer.cancel()
        print(f"{time.perf_counter() - started:8.2f}s  warmed the {what}", flush=True)


def report(conn: psycopg.Connection[Any], schema: str) -> list[str]:
    """What the Dataset holds, from the facts and the roll-up."""
    facts = sql.Identifier(schema, FACTS_TABLES["otel_demo"])
    lines = []
    for kind, _, n, first, last in conn.execute(
        sql.SQL("SELECT kind, value, n, first, last FROM {} WHERE kind = 'range'").format(facts)
    ).fetchall():
        lines.append(f"otel_demo: {n:,} Log Entries from {first} to {last} (ts in UTC) [{kind}]")
    minute = sql.Identifier(schema, SIGNAL_TABLES["otel_demo"]["metric_minutes"])
    baseline = sql.Identifier(schema, SIGNAL_TABLES["otel_demo"]["metric_baselines"])
    rows = conn.execute(
        sql.SQL("SELECT (SELECT count(*) FROM {}), (SELECT count(*) FROM {})").format(minute, baseline)
    ).fetchone()
    if rows is not None:
        lines.append(f"metric roll-up: {rows[0]:,} minute rows over {rows[1]:,} series with baselines")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schema", help="target schema (default SLM_SCHEMA)")
    parser.add_argument("--url", help="the otel_demo directory (default SLM_OTEL_DEMO_URL)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds per statement")
    parser.add_argument("--no-warm", action="store_true", help="skip warming the TraceId bloom filters")
    args = parser.parse_args(argv)
    try:
        overrides = {
            key: value for key, value in (("slm_schema", args.schema), ("otel_demo_url", args.url)) if value
        }
        settings = load_settings(**overrides)
    except ConfigError as exc:
        print(f"otel-setup: {exc}", file=sys.stderr)
        return 2
    schema, files = settings.slm_schema, settings.otel_demo_files()
    try:
        with psycopg.connect(
            settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S, prepare_threshold=None
        ) as conn:
            zone_row = conn.execute("SELECT current_setting('TimeZone')").fetchone()
            zone = str(zone_row[0]) if zone_row else ""
            if not is_utc(zone):
                raise DeriveError(
                    f"the session time zone is {zone!r}, not UTC; the otel_demo views cast the OTel "
                    "timestamps in the session time zone, so ts would not be UTC. Unset PGTZ or connect "
                    "with TimeZone=UTC."
                )
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            print(f"otel_demo from {settings.otel_demo_url} into schema {schema}")
            run_file(conn, SETUP_SQL, variables(schema, files), args.timeout)
            for line in report(conn, schema):
                print(line)
            if not args.no_warm:
                warm(conn, schema, args.timeout)
    except DeriveError as exc:
        print(f"otel-setup: {exc}", file=sys.stderr)
        return 2
    except psycopg.OperationalError as exc:
        print(f"otel-setup: lost or refused connection ({exc}); run it again", file=sys.stderr)
        return 1
    print(
        f"done; get_trace, count_by and trend now accept dataset='otel_demo' ({DATASETS['otel_demo'].title})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
