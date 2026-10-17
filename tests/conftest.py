"""Test markers and their prerequisites.

Unmarked tests are pure and run everywhere (`make test`).

- `db`: needs a reachable SereneDB at DATABASE_URL (`make test-db`).
- `corpus`: needs the full Base Corpus in the raw tables of SLM_RAW_SCHEMA (`make test-corpus`).
- `embedding_model`: uses the real fastembed model of find_patterns (the optional `vectors` extra
  and a model in the cache, from `make embed-patterns`); skips otherwise, even with
  SLM_REQUIRE_DB. Every other test runs with `fake_embedder.FakeEmbedder`, so no test downloads
  a model.
- `loghub2(*tables)`: also reads the optional Loghub-2.0 label tables (default lh2_bgl and
  lh2_openstack) from SLM_RAW_SCHEMA. Put it on a db or corpus test.

Tests under tests/db/ and tests/corpus/ get the marker of their directory automatically.

A missing prerequisite skips the test with the reason. Set SLM_REQUIRE_DB=1 (CI, PR gates) to turn
the skips for the database, the Base Corpus and the derived schema into failures. A missing
optional Loghub-2.0 file or table stays a skip even then (#66). Later fixtures go into
per-directory conftest.py files (plan section 5), not here.

Which data each suite needs, so that a missing optional download is not mistaken for a failure:

- `make test`: nothing. tests/unit/test_fetch_data.py checks each Zenodo record that SLM_DATA_DIR
  holds any file of, and skips a record it holds none of.
- `make test-db`: SereneDB, and the Loghub 2k samples (downloaded into SLM_DATA_DIR/sample on first
  use). The Pattern Tier 1 tests also need the Loghub-2.0 template files in SLM_DATA_DIR/lh2
  (`make fetch-data`). Without them every derive in the db tests runs with SLM_PATTERN_TIER1=0 and
  only the Tier 1 tests skip.
- `make test-corpus`: the full Base Corpus (Zenodo 8196385; `make fetch-data`, `make load-dev`) and
  a `make derive` into SLM_SCHEMA. tests/corpus/test_patterns_corpus.py also needs Loghub-2.0
  (Zenodo 8275861): Tier 1 Patterns in SLM_SCHEMA, or the template files to derive them, and, for
  the scorer test, the tables lh2_bgl and lh2_openstack (`make load-dev`).
"""

from __future__ import annotations

import functools
import os
import re
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fake_embedder import FakeEmbedder
from optional_data import LOGHUB2_TABLES
from psycopg import sql

from serenedb_log_mcp.config import load_settings, safe_dsn
from serenedb_log_mcp.corpus import embeddings

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
        "embedding_model: uses the real fastembed model of find_patterns; skips when the optional "
        "vectors extra or its cached model is missing, even with SLM_REQUIRE_DB",
    )
    config.addinivalue_line(
        "markers",
        "loghub2(*tables): also reads these optional Loghub-2.0 tables from SLM_RAW_SCHEMA (default: "
        f"{', '.join(LOGHUB2_TABLES)}); skips with the reason when one is missing, even with SLM_REQUIRE_DB",
    )


@pytest.fixture(autouse=True)
def _embedder(request: pytest.FixtureRequest) -> Iterator[None]:
    """The fake embedder for every test, the real one (or a skip) for `embedding_model` tests."""
    if request.node.get_closest_marker("embedding_model") is None:
        embeddings.set_embedder(FakeEmbedder())
    else:
        embeddings.set_embedder(None)
        if (problem := _embedding_problem()) is not None:
            pytest.skip(problem)
    yield
    embeddings.set_embedder(None)


@functools.cache
def _embedding_problem() -> str | None:
    """Why the real embedding model cannot run here, or None. An optional install, so always a skip."""
    try:
        embeddings.FastEmbedder()
    except embeddings.EmbeddingUnavailable as exc:
        return f"optional embedding model unavailable: {exc}"
    return None


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
    corpus = item.get_closest_marker("corpus") is not None
    if not corpus and item.get_closest_marker("db") is None:
        return
    # Resolved here, so the verdicts below are cached per DSN and schema rather than once per
    # session: a test that changes DATABASE_URL or SLM_RAW_SCHEMA gets a fresh verdict.
    settings = load_settings()
    problem = _db_problem(settings.database_url)
    if problem is None and corpus:
        problem = _corpus_problem(settings.database_url, settings.raw_schema)
    if problem is not None:
        if _require_db():
            pytest.fail(f"SLM_REQUIRE_DB is set, so this does not skip: {problem}", pytrace=False)
        pytest.skip(problem)
    tables = {t for marker in item.iter_markers("loghub2") for t in (marker.args or LOGHUB2_TABLES)}
    if not tables:
        return
    optional = _loghub2_problem(settings.database_url, settings.raw_schema, tuple(sorted(tables)))
    if optional is not None:
        # An optional download, so a skip even under SLM_REQUIRE_DB.
        pytest.skip(optional)


def _require_db() -> bool:
    return os.environ.get("SLM_REQUIRE_DB", "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def database_url() -> str:
    """The DSN that db and corpus tests connect to."""
    return load_settings().database_url


# Why a connection failed, in the order the patterns are tried: a privilege refusal mentions the
# database too, so it has to be matched before the wrong-name case. Measured against SereneDB
# 26.09.1.3: every one of these arrives as a psycopg.OperationalError with no sqlstate at all, so
# the libpq text is the only thing that tells them apart. `_connect_problem` keeps the sqlstate
# whenever there is one, which is the case for a statement that is refused after connecting.
_CONNECT_PROBLEMS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"permission denied for database|CONNECT privilege", re.IGNORECASE),
        "SereneDB at {dsn} refused the connection for lack of privileges",
        "The role needs CONNECT on that database; grant it, or name another role in DATABASE_URL.",
    ),
    (
        re.compile(r'database ".*" (?:does not exist|is not accessible)', re.IGNORECASE),
        "SereneDB at {dsn} has no database of that name",
        "Check `dbname` in DATABASE_URL; `make db-up` serves `postgres`.",
    ),
    (
        re.compile(
            r'password authentication failed|no password supplied|role ".*" does not exist'
            r"|pg_hba\.conf|authentication method",
            re.IGNORECASE,
        ),
        "SereneDB at {dsn} refused the credentials",
        "Check `user` and `password` in DATABASE_URL (or DATABASE_URL_<ROLE>); "
        "`make db-up` starts a server with trust auth.",
    ),
    (
        re.compile(
            r"connection refused|could not connect to server|no route to host|not known"
            r"|nodename nor servname|timeout expired|operation timed out",
            re.IGNORECASE,
        ),
        "SereneDB not reachable at {dsn}: nothing is listening there",
        "Start one with `make db-up` or point DATABASE_URL at a running instance.",
    ),
)


def _connect_problem(dsn: str, exc: psycopg.Error) -> str:
    """One skip reason that says which of the usual causes it was, with the sqlstate if there is one.

    The whole libpq message is kept on one line, because the cause is sometimes only in the DETAIL
    line (a missing CONNECT privilege says so there). It never carries the password: libpq names
    the host, the port and the user, and `safe_dsn` redacts the DSN itself.
    """
    message = " ".join(str(exc).split()) or type(exc).__name__
    detail = f"{message} sqlstate={exc.sqlstate}" if exc.sqlstate else message
    target = safe_dsn(dsn)
    for pattern, headline, hint in _CONNECT_PROBLEMS:
        if pattern.search(message):
            return f"{headline.format(dsn=target)} ({detail}). {hint}"
    return (
        f"SereneDB not reachable at {target} ({detail}). "
        "Start one with `make db-up` or point DATABASE_URL at a running instance."
    )


@functools.cache
def _db_problem(dsn: str) -> str | None:
    """Can we connect to `dsn` and run `SELECT 1`? Cached per DSN, so a changed DSN is retried."""
    try:
        with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        return _connect_problem(dsn, exc)
    return None


@functools.cache
def _corpus_problem(dsn: str, schema: str) -> str | None:
    """Is the full Base Corpus in `schema`? Cached per DSN and schema, not once per session."""
    with psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as conn:
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


@functools.cache
def _loghub2_problem(database_url: str, schema: str, tables: tuple[str, ...]) -> str | None:
    """Which of the optional Loghub-2.0 `tables` are missing in `schema`, or None."""
    with psycopg.connect(database_url, connect_timeout=CONNECT_TIMEOUT_S, autocommit=True) as conn:
        present = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", [schema]
            ).fetchall()
        }
    missing = [f"{schema}.{t} is missing" for t in tables if t not in present]
    if not missing:
        return None
    return (
        f"optional Loghub-2.0 labels missing: {'; '.join(missing)}. Fetch Zenodo 8275861 with "
        f'`make fetch-data`, then load them with `make load-dev LOAD_ARGS="{" ".join(LOGHUB2_TABLES)}"`.'
    )
