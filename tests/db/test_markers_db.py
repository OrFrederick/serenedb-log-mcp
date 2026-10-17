"""tests/conftest.py against a real SereneDB: how the corpus gate decides between run and skip.

Every table here lives in a throwaway scratch schema; the shared `public` tables are never touched.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

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


LOGHUB2_SUITE = """
import pytest

@pytest.mark.corpus
@pytest.mark.loghub2("lh2_bgl", "lh2_openstack")
def test_reads_the_labels():
    pass

@pytest.mark.corpus
def test_reads_the_base_corpus_only():
    pass
"""


def test_missing_loghub2_tables_skip_only_the_tests_that_read_them_even_when_required(
    suite: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, database_url: str, raw_schema: str
) -> None:
    # Loghub-2.0 (Zenodo 8275861) is an optional download: SLM_REQUIRE_DB stays strict for the
    # database and the Base Corpus, but a missing label table is a skip.
    make_table(database_url, raw_schema, "bgl", 1_000_001, COLUMNS["bgl"])
    make_table(database_url, raw_schema, "openstack", 100_001, COLUMNS["openstack"])
    make_table(database_url, raw_schema, "lh2_bgl", 10, ["content", "event_id"])
    monkeypatch.setenv("SLM_RAW_SCHEMA", raw_schema)
    monkeypatch.setenv("SLM_REQUIRE_DB", "1")
    suite.makepyfile(test_loghub2=LOGHUB2_SUITE)

    without = suite.runpytest("-rs", "test_loghub2.py")
    make_table(database_url, raw_schema, "lh2_openstack", 10, ["content", "event_id"])
    with_labels = suite.runpytest("-rs", "test_loghub2.py")

    without.assert_outcomes(passed=1, skipped=1)
    without.stdout.fnmatch_lines(
        [f"SKIPPED*optional Loghub-2.0*{raw_schema}.lh2_openstack is missing*make fetch-data*make load-dev*"]
    )
    assert f"{raw_schema}.lh2_bgl " not in without.stdout.str()
    with_labels.assert_outcomes(passed=2)
