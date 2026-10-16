"""Test markers and their prerequisites.

Unmarked tests are pure and run everywhere (`make test`).

- `db`: needs a reachable SereneDB at DATABASE_URL (`make test-db`).
- `corpus`: needs the full Base Corpus in the raw tables of SLM_RAW_SCHEMA (`make test-corpus`).

A missing prerequisite skips the test with the reason; it never fails it. Later fixtures go into
per-directory conftest.py files (plan section 5), not here.
"""

from __future__ import annotations

import functools

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from serenedb_log_mcp.config import load_settings

# Row counts of the raw tables after `make load-dev` (dev loader, Zenodo 8196385).
BASE_CORPUS_ROWS = {"bgl": 4_747_963, "openstack": 207_820}
CONNECT_TIMEOUT_S = 3


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "db: needs a reachable SereneDB at DATABASE_URL; skips with the reason otherwise"
    )
    config.addinivalue_line(
        "markers",
        "corpus: needs the full Base Corpus (raw bgl and openstack tables in SLM_RAW_SCHEMA); "
        "skips with the reason otherwise",
    )


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("corpus") is not None:
        if problem := _db_problem() or _corpus_problem():
            pytest.skip(problem)
    elif item.get_closest_marker("db") is not None and (problem := _db_problem()):
        pytest.skip(problem)


@pytest.fixture(scope="session")
def database_url() -> str:
    """The DSN that db and corpus tests connect to."""
    return load_settings().database_url


def _safe_dsn(dsn: str) -> str:
    """The DSN without its password, for skip reasons and logs."""
    try:
        parts = conninfo_to_dict(dsn)
    except psycopg.ProgrammingError:
        return "<unparseable DATABASE_URL>"
    return " ".join(f"{key}={value}" for key, value in parts.items() if key != "password")


@functools.cache
def _db_problem() -> str | None:
    dsn = load_settings().database_url
    try:
        with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return (
            f"SereneDB not reachable at {_safe_dsn(dsn)} ({detail}). "
            "Start one with `make db-up` or point DATABASE_URL at a running instance."
        )
    return None


@functools.cache
def _corpus_problem() -> str | None:
    settings = load_settings()
    schema = settings.raw_schema
    with psycopg.connect(settings.database_url, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as conn:
        for table, expected in BASE_CORPUS_ROWS.items():
            exists = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                [schema, table],
            ).fetchone()
            if not exists or exists[0] == 0:
                return f"full Base Corpus missing: {schema}.{table} is missing. Load it with `make load-dev`."
            row = conn.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()
            rows = row[0] if row else 0
            if rows != expected:
                return (
                    f"full Base Corpus missing: {schema}.{table} has {rows:,} rows, expected {expected:,}. "
                    "Load it with `make load-dev`."
                )
    return None
