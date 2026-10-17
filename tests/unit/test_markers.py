"""tests/conftest.py: the db and corpus markers skip with a clear reason when a prerequisite is missing."""

import tomllib
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).parents[2]
ROOT_CONFTEST = Path(__file__).parents[1] / "conftest.py"
UNREACHABLE = "host=127.0.0.1 port=9 dbname=postgres user=postgres"

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


@pytest.fixture
def suite(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.Pytester:
    monkeypatch.delenv("SLM_REQUIRE_DB", raising=False)
    monkeypatch.delenv("SLM_RAW_SCHEMA", raising=False)
    pytester.makeconftest(ROOT_CONFTEST.read_text())
    pytester.makepyfile(test_suite=SUITE)
    return pytester


def test_markers_are_registered(suite: pytest.Pytester) -> None:
    result = suite.runpytest("--markers")

    result.stdout.fnmatch_lines(["@pytest.mark.db:*", "@pytest.mark.corpus:*"])


def test_unreachable_db_skips_db_and_corpus_tests_with_reason(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)

    result = suite.runpytest("-rs", "--strict-markers")

    result.assert_outcomes(passed=1, skipped=2)
    skips = [line for line in result.outlines if line.startswith("SKIPPED")]
    assert skips
    for line in skips:
        assert "SereneDB not reachable at" in line
        assert "host=127.0.0.1 port=9" in line
        assert "make db-up" in line


def test_skip_reason_never_shows_the_password(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE + " password=hunter2")

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=1, skipped=2)
    assert "hunter2" not in result.stdout.str()


@pytest.mark.db
def test_missing_corpus_tables_skip_corpus_tests_only(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLM_RAW_SCHEMA", "slm_no_such_schema")

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines(["SKIPPED*full Base Corpus missing: slm_no_such_schema.bgl is missing*"])


@pytest.mark.parametrize("value", ["1", "true"])
def test_require_db_turns_the_skips_into_failures(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    monkeypatch.setenv("SLM_REQUIRE_DB", value)

    result = suite.runpytest("-ra")

    # A failure in test setup is reported as an error; either way the run fails.
    result.assert_outcomes(passed=1, errors=2)
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*SLM_REQUIRE_DB is set, so this does not skip: SereneDB not reachable at*"])


def test_tests_under_db_and_corpus_directories_get_the_markers(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    suite.makepyfile(**{"db/test_in_db_dir": "def test_a():\n    pass\n"})
    suite.makepyfile(**{"corpus/test_in_corpus_dir": "def test_b():\n    pass\n"})

    only_db = suite.runpytest("-m", "db", "-rs", "db", "corpus")
    only_corpus = suite.runpytest("-m", "corpus", "-rs", "db", "corpus")

    only_db.assert_outcomes(skipped=1, deselected=1)
    only_db.stdout.fnmatch_lines(["*db/test_in_db_dir.py*"])
    only_corpus.assert_outcomes(skipped=1, deselected=1)
    only_corpus.stdout.fnmatch_lines(["*corpus/test_in_corpus_dir.py*"])


def test_same_test_file_name_in_two_directories_collects(pytester: pytest.Pytester) -> None:
    ini = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    pytester.makeini("[pytest]\naddopts = " + " ".join(ini["addopts"]) + "\n")
    pytester.makepyfile(
        **{"a/test_same": "def test_a():\n    pass\n", "b/test_same": "def test_b():\n    pass\n"}
    )

    result = pytester.runpytest()

    result.assert_outcomes(passed=2)


@pytest.fixture
def raw_schema(database_url: str) -> Iterator[str]:
    schema = f"scratch_t1_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    yield schema
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def make_table(database_url: str, schema: str, table: str, rows: int) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE TABLE {} AS SELECT g AS line_no FROM generate_series(1, {}) AS s(g)").format(
                sql.Identifier(schema, table), sql.Literal(rows)
            )
        )


@pytest.mark.db
def test_corpus_gate_runs_tests_when_counts_differ_but_clear_the_floor(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str, raw_schema: str
) -> None:
    # The gate must let corpus tests run (and fail on wrong golden numbers) instead of skipping.
    make_table(database_url, raw_schema, "bgl", 1_000_001)
    make_table(database_url, raw_schema, "openstack", 100_001)
    monkeypatch.setenv("SLM_RAW_SCHEMA", raw_schema)

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=3)


@pytest.mark.db
def test_corpus_gate_skips_tables_below_the_floor(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str, raw_schema: str
) -> None:
    make_table(database_url, raw_schema, "bgl", 2_000)
    make_table(database_url, raw_schema, "openstack", 2_000)
    monkeypatch.setenv("SLM_RAW_SCHEMA", raw_schema)

    result = suite.runpytest("-rs")

    result.assert_outcomes(passed=2, skipped=1)
    result.stdout.fnmatch_lines([f"SKIPPED*{raw_schema}.bgl has 2,000 rows*more than 1,000,000*"])
