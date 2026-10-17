#!/usr/bin/env python3
"""Register the remote Dataset text_bench in a SereneDB schema (T20).

Usage (see `make help`):
    uv run python scripts/huge_setup.py [--schema S] [--url URL] [--timeout SECONDS]

Runs sql/remote/text_bench.sql inside SLM_SCHEMA (default slm), which creates:
    tb_entry   a view over read_parquet(URL) with canonical column names (no data is copied)
    tb_facts   the Dataset's time range and row count (from the Parquet footer) and its Components
               and levels with their counts (one pass over two narrow columns of the whole file)

and, when the schema has none yet, the `log_text` text search dictionary that agent indexes use.
URL is SLM_TEXT_BENCH_URL, by default text_bench/part_000.parquet (1,000,000,000 rows, 41 GB). The
SereneDB server reads the file itself, so the server needs outbound HTTPS; nothing here downloads
it. The facts pass reads about 512 MiB and took 75 s on the shared dev server (12 threads).

It never indexes the file: agent indexes over time slices come from the create_index tool. It
re-runs safely (CREATE OR REPLACE), but stops when an agent index exists on the view, because
replacing the view under an index is not something to do silently: call drop_index first.

Environment: DATABASE_URL, SLM_SCHEMA, SLM_TEXT_BENCH_URL.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from derive import (
    SQL_DIR,
    DeriveError,
    StepFailed,
    dictionary_ddl,
    render,
    serenedb_version,
    split_statements,
)
from serenedb_log_mcp.config import ConfigError, load_settings
from serenedb_log_mcp.corpus.datasets import DATASETS, ENTRY_TABLES, REMOTE_FACTS, TEXT_DICTIONARY

SETUP_SQL = SQL_DIR / "remote" / "text_bench.sql"
CONNECT_TIMEOUT_S = 10
DEFAULT_TIMEOUT_S = 900.0
UTC_ZONES = frozenset({"utc", "etc/utc", "gmt", "etc/gmt", "zulu", "etc/zulu", "universal", "etc/universal"})


def variables(schema: str, url: str) -> dict[str, str]:
    """Placeholder values: the quoted schema and the file URL as a SQL string literal."""
    return {"slm": sql.Identifier(schema).as_string(None), "url": sql.Literal(url).as_string(None)}


def is_utc(zone: str) -> bool:
    """Does the session time zone name UTC (the view's Timestamp cast depends on it)?"""
    return zone.strip().lower() in UTC_ZONES


def run_file(
    conn: psycopg.Connection[Any], path: Path, values: Mapping[str, str], timeout_s: float
) -> list[tuple[str, float]]:
    """Run every statement of `path`, each cancelled after `timeout_s`; returns (statement, seconds)."""
    timings = []
    for statement in split_statements(render(path.read_text(), values)):
        summary = " ".join(statement.split())[:90]
        timer = threading.Timer(timeout_s, conn.cancel_safe)
        timer.daemon = True
        started = time.perf_counter()
        timer.start()
        try:
            conn.execute(statement)
        except psycopg.Error as exc:
            raise StepFailed(path.name, summary, exc) from exc
        finally:
            timer.cancel()
        seconds = time.perf_counter() - started
        timings.append((summary, seconds))
        print(f"{seconds:8.2f}s  {summary}", flush=True)
    return timings


def ensure_dictionary(conn: psycopg.Connection[Any], schema: str) -> bool:
    """Create the log_text dictionary in `schema` unless it exists; True when it was created."""
    found = conn.execute(
        "SELECT count(*) FROM pg_ts_dict AS d JOIN pg_namespace AS n ON n.oid = d.dictnamespace "
        "WHERE n.nspname = %s AND d.dictname = %s",
        [schema, TEXT_DICTIONARY],
    ).fetchone()
    if found is not None and found[0]:
        return False
    row = conn.execute("SELECT version()").fetchone()
    version = serenedb_version(str(row[0]) if row else "")
    conn.execute(dictionary_ddl(sql.Identifier(schema, TEXT_DICTIONARY).as_string(None), version))
    return True


def agent_indexes_on_view(conn: psycopg.Connection[Any], schema: str) -> list[str]:
    """Agent indexes over the text_bench view in `schema` (pg_indexes does not list them)."""
    rows = conn.execute(
        "SELECT c.relname FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relkind = 'i' AND c.relname LIKE 'agent_idx_text_bench_%%'",
        [schema],
    ).fetchall()
    return [str(name) for (name,) in rows]


def report(conn: psycopg.Connection[Any], schema: str) -> list[str]:
    """What the Dataset holds, from the facts table."""
    facts = sql.Identifier(schema, REMOTE_FACTS)
    rows = conn.execute(
        sql.SQL("SELECT kind, value, n, first, last FROM {} ORDER BY kind, n DESC").format(facts)
    ).fetchall()
    lines = []
    for kind, _, n, first, last in rows:
        if kind == "range":
            lines.append(f"text_bench: {n:,} Log Entries from {first} to {last} (ts in UTC)")
    for wanted in ("component", "level"):
        values = [(value, n) for kind, value, n, _, _ in rows if kind == wanted]
        shown = ", ".join(f"{value or '(empty)'} {n:,}" for value, n in values[:8])
        lines.append(f"{wanted}s ({len(values)}): {shown}{' ...' if len(values) > 8 else ''}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--schema", help="target schema (default SLM_SCHEMA)")
    parser.add_argument("--url", help="the Parquet file (default SLM_TEXT_BENCH_URL)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="seconds per statement")
    args = parser.parse_args(argv)
    try:
        overrides = {
            key: value for key, value in (("slm_schema", args.schema), ("text_bench_url", args.url)) if value
        }
        settings = load_settings(**overrides)
    except ConfigError as exc:
        print(f"huge-setup: {exc}", file=sys.stderr)
        return 2
    schema, url = settings.slm_schema, settings.text_bench_url
    view = ENTRY_TABLES["text_bench"]
    try:
        with psycopg.connect(
            settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S, prepare_threshold=None
        ) as conn:
            zone_row = conn.execute("SELECT current_setting('TimeZone')").fetchone()
            zone = str(zone_row[0]) if zone_row else ""
            if not is_utc(zone):
                raise DeriveError(
                    f"the session time zone is {zone!r}, not UTC; the text_bench view casts the OTel "
                    "Timestamp in the session time zone, so ts would not be UTC. Unset PGTZ or connect "
                    "with TimeZone=UTC."
                )
            if blocking := agent_indexes_on_view(conn, schema):
                raise DeriveError(
                    f"{schema}.{view} has agent indexes ({', '.join(blocking)}); drop them with drop_index "
                    "before replacing the view."
                )
            conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            if ensure_dictionary(conn, schema):
                print(f"created the text search dictionary {schema}.{TEXT_DICTIONARY}")
            print(f"text_bench from {url} into {schema}.{view} and {schema}.{REMOTE_FACTS}")
            run_file(conn, SETUP_SQL, variables(schema, url), args.timeout)
            for line in report(conn, schema):
                print(line)
    except DeriveError as exc:
        print(f"huge-setup: {exc}", file=sys.stderr)
        return 2
    except psycopg.OperationalError as exc:
        print(f"huge-setup: lost or refused connection ({exc}); run it again", file=sys.stderr)
        return 1
    print(
        "done; count_by, trend and create_index now accept dataset='text_bench' "
        f"({DATASETS['text_bench'].title})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
