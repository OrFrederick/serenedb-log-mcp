"""tests/conftest.py against a real SereneDB: how the corpus gate decides between run and skip.

Every table here lives in a throwaway scratch schema; the shared `public` tables are never touched.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT_CONFTEST = Path(__file__).parents[1] / "conftest.py"

SUITE = """
import pytest

def test_pure():
    pass

@pytest.mark.db
def test_needs_db():
    pass

@pytest.mark.corpus
def test_needs_corpus():
    pass
"""

# The columns tests/corpus reads; the gate requires them (see REQUIRED_COLUMNS in tests/conftest.py).
COLUMNS = {
    "bgl": ["alert_tag", "ts", "node", "component", "level", "message"],
    "openstack": ["ts", "pid", "component", "level", "message"],
}


@pytest.fixture
def suite(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    monkeypatch.delenv("SLM_REQUIRE_DB", raising=False)
    monkeypatch.delenv("SLM_RAW_SCHEMA", raising=False)
    pytester.makeconftest(ROOT_CONFTEST.read_text())
    pytester.makepyfile(test_suite=SUITE)
    return pytester


@pytest.fixture
def raw_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t1_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    yield schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def make_table(database_url: str, schema: str, table: str, rows: int, columns: list[str]) -> None:
    """A raw table with `rows` rows, a line_no column and NULL VARCHAR `columns`."""
    extra = [sql.SQL("CAST(NULL AS VARCHAR) AS {}").format(sql.Identifier(c)) for c in columns]
    select = sql.SQL(", ").join([sql.SQL("g AS line_no"), *extra])
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {} AS SELECT {} FROM generate_series(1, {}) AS s(g)").format(
                sql.Identifier(schema, table), select, sql.Literal(rows)
            )
        )


def test_missing_corpus_tables_skip_corpus_tests_only(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLM_RAW_SCHEMA", "slm_no_such_schema")

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines(["SKIPPED*full Base Corpus missing: slm_no_such_schema.bgl is missing*"])


def test_corpus_gate_runs_tests_when_counts_differ_but_clear_the_floor(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str, raw_schema: str
) -> None:
    # The gate must let corpus tests run (and fail on wrong golden numbers) instead of skipping.
    make_table(database_url, raw_schema, "bgl", 1_000_001, COLUMNS["bgl"])
    make_table(database_url, raw_schema, "openstack", 100_001, COLUMNS["openstack"])
    monkeypatch.setenv("SLM_RAW_SCHEMA", raw_schema)

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=3)


def test_corpus_gate_skips_tables_below_the_floor(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str, raw_schema: str
) -> None:
    make_table(database_url, raw_schema, "bgl", 2_000, COLUMNS["bgl"])
    make_table(database_url, raw_schema, "openstack", 2_000, COLUMNS["openstack"])
    monkeypatch.setenv("SLM_RAW_SCHEMA", raw_schema)

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines([f"SKIPPED*{raw_schema}.bgl has 2,000 rows*more than 1,000,000*"])


def test_corpus_gate_skips_tables_from_a_different_loader(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str, raw_schema: str
) -> None:
    # An earlier loader named the columns alert_label and content instead of alert_tag and message.
    other_loader = ["alert_label", "ts", "node", "component", "level", "content"]
    make_table(database_url, raw_schema, "bgl", 10, other_loader)
    make_table(database_url, raw_schema, "openstack", 10, COLUMNS["openstack"])
    monkeypatch.setenv("SLM_RAW_SCHEMA", raw_schema)

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines(
        [f"SKIPPED*{raw_schema}.bgl has no column alert_tag*different loader*Reload with `make load-dev`*"]
    )


# A conftest that rewrites SLM_RAW_SCHEMA between the two tests, to prove the corpus verdict is
# cached per schema and not once per session. The copied root hook is called at the end.
PER_TEST_SCHEMA = """
import os

_root_gate = pytest_runtest_setup
PER_TEST = {"test_first": "slm_no_schema_a", "test_second": "slm_no_schema_b"}


def pytest_runtest_setup(item):
    os.environ["SLM_RAW_SCHEMA"] = PER_TEST[item.name]
    _root_gate(item)
"""

TWO_CORPUS_TESTS = """
import pytest

@pytest.mark.corpus
def test_first():
    pass

@pytest.mark.corpus
def test_second():
    pass
"""


def test_the_corpus_verdict_is_cached_per_schema_not_per_session(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLM_REQUIRE_DB", raising=False)
    monkeypatch.setenv("SLM_RAW_SCHEMA", "slm_no_schema_a")
    pytester.makeconftest(ROOT_CONFTEST.read_text() + PER_TEST_SCHEMA)
    pytester.makepyfile(test_suite=TWO_CORPUS_TESTS)

    result = pytester.runpytest("-rs")

    result.assert_outcomes(skipped=2)
    skips = [line for line in result.outlines if line.startswith("SKIPPED")]
    assert len(skips) == 2, skips
    # The second test changed the schema, so it must get its own verdict, not the first one's.
    assert "slm_no_schema_a.bgl" in skips[0]
    assert "slm_no_schema_b.bgl" in skips[1]


@pytest.fixture
def no_connect_privilege(database_url: str) -> Iterator[str]:
    """A DSN for a real database that the role in it may not connect to; both dropped afterwards.

    Own database and own role, so the shared `postgres` database and PUBLIC keep their grants. The
    role gets a password, because a server that asks for one refuses a password-less role before it
    ever checks CONNECT: that is the credentials case, not this one.
    """
    suffix = uuid.uuid4().hex[:8]
    database = f"scratch_gate_{suffix}"
    role = f"scratch_gate_{suffix}_role"
    secret = f"pw_{uuid.uuid4().hex}"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database)))
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(secret))
        )
    parts = conninfo_to_dict(database_url)
    yield make_conninfo("", **{**parts, "dbname": database, "user": role, "password": secret})
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
        conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


def _other_dbname(database_url: str, dbname: str) -> str:
    parts = conninfo_to_dict(database_url)
    return make_conninfo("", **{**parts, "dbname": dbname})


def _other_user(database_url: str, user: str) -> str:
    parts = conninfo_to_dict(database_url)
    parts.pop("password", None)
    return make_conninfo("", **{**parts, "user": user})


def test_a_wrong_database_name_is_told_from_an_absent_server(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", _other_dbname(database_url, "slm_no_such_database"))

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.fnmatch_lines(["SKIPPED*has no database of that name*`dbname` in DATABASE_URL*"])


def test_a_user_that_does_not_exist_is_a_credentials_problem(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", _other_user(database_url, "slm_no_such_role"))

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.fnmatch_lines(["SKIPPED*refused the credentials*`user` and `password`*"])


def test_a_role_without_connect_privilege_is_told_from_bad_credentials(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, no_connect_privilege: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", no_connect_privilege)

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=1, skipped=2)
    result.stdout.fnmatch_lines(["SKIPPED*refused the connection for lack of privileges*needs CONNECT*"])
    secret = conninfo_to_dict(no_connect_privilege)["password"]
    assert isinstance(secret, str)
    assert secret not in result.stdout.str()
