"""tests/conftest.py: the db and corpus markers skip with a clear reason when a prerequisite is missing."""

from pathlib import Path

import pytest

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
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
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
    assert len(skips) == 2
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
