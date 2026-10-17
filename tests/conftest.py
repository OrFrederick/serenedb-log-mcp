"""Test markers and their prerequisites.

Unmarked tests are pure and run everywhere (`make test`).

- `db`: needs a reachable SereneDB at DATABASE_URL (`make test-db`).
- `corpus`: needs the full Base Corpus in the raw tables of SLM_RAW_SCHEMA (`make test-corpus`).
- `remote`: reads the public S3 text_bench file from the SereneDB server (T20). Skipped unless
  SLM_REMOTE_TESTS=1, also under SLM_REQUIRE_DB=1, because it depends on S3 and is slow
  (`make test-remote`). Mark such tests `db` too, so `make test` never collects them.

Tests under tests/db/ and tests/corpus/ get the marker of their directory automatically.

A missing prerequisite skips the test with the reason. Set SLM_REQUIRE_DB=1 (CI, PR gates) to turn
those skips into failures. Later fixtures go into per-directory conftest.py files (plan section 5),
not here.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from serenedb_log_mcp.config import load_settings

# The corpus gate only checks that the raw tables exist and are clearly the full corpus, not a
# 2k sample. The exact golden numbers (4,747,963 and 207,820 rows) live in the corpus tests, so a
# wrong load makes them fail instead of skipping them.
BASE_CORPUS_FLOOR = {"bgl": 1_000_000, "openstack": 100_000}
# The columns tests/corpus reads. A table without them came from a different loader, so the corpus
# tests skip with a reason instead of failing with UndefinedColumn.
REQUIRED_COLUMNS = {
    "bgl": ("alert_tag", "ts", "node", "component", "level", "message"),
    "openstack": ("ts", "pid", "component", "level", "message"),
}
CONNECT_TIMEOUT_S = 3
MARKER_DIRS = {"db": "db", "corpus": "corpus"}
TESTS_DIR = Path(__file__).parent


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "db: needs a reachable SereneDB at DATABASE_URL; skips with the reason otherwise"
    )
    config.addinivalue_line(
        "markers",
        "corpus: needs the full Base Corpus (raw bgl and openstack tables in SLM_RAW_SCHEMA); "
        "skips with the reason otherwise",
    )
    config.addinivalue_line(
        "markers",
        "remote: reads the public S3 text_bench file from the SereneDB server; skipped unless "
        "SLM_REMOTE_TESTS=1 (make test-remote)",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark tests by directory before `-m` selection runs: tests/db/* is db, tests/corpus/* is corpus."""
    for item in items:
        try:
            top = item.path.relative_to(TESTS_DIR).parts[0]
        except ValueError:
            continue
        if (marker := MARKER_DIRS.get(top)) is not None and item.get_closest_marker(marker) is None:
            item.add_marker(marker)


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("remote") is not None and not _flag("SLM_REMOTE_TESTS"):
        pytest.skip(
            "reads the public S3 text_bench file; set SLM_REMOTE_TESTS=1 (make test-remote) to run it"
        )
    problem = None
    if item.get_closest_marker("corpus") is not None:
        problem = _db_problem() or _corpus_problem()
    elif item.get_closest_marker("db") is not None:
        problem = _db_problem()
    if problem is None:
        return
    if _require_db():
        pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
    pytest.skip(problem)


def _require_db() -> bool:
    return _flag("SLM_REQUIRE_DB")


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


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
        for table, floor in BASE_CORPUS_FLOOR.items():
            exists = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                [schema, table],
            ).fetchone()
            if not exists or exists[0] == 0:
                return f"full Base Corpus missing: {schema}.{table} is missing. Load it with `make load-dev`."
            present = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s",
                    [schema, table],
                ).fetchall()
            }
            missing = [c for c in REQUIRED_COLUMNS[table] if c not in present]
            if missing:
                return (
                    f"full Base Corpus missing: {schema}.{table} has no column {', '.join(missing)}; it was "
                    "loaded by a different loader. Reload with `make load-dev`."
                )
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
            ).fetchone()
            rows = row[0] if row else 0
            if rows <= floor:
                return (
                    f"full Base Corpus missing: {schema}.{table} has {rows:,} rows; the full corpus has "
                    f"more than {floor:,}. Load it with `make load-dev`."
                )
    return None
