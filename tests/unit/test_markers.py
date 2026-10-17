"""tests/conftest.py: the db and corpus markers skip with a clear reason when a prerequisite is missing."""

import tomllib
from pathlib import Path

import pytest

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


# A conftest that rewrites DATABASE_URL between the two tests, to prove the gate's verdict is
# cached per resolved DSN and not once per session. The copied root hook is called at the end.
PER_TEST_DSN = """
import os

_root_gate = pytest_runtest_setup
PER_TEST = {
    "test_first": "host=127.0.0.1 port=9 dbname=postgres user=postgres",
    "test_second": "host=127.0.0.1 port=10 dbname=postgres user=postgres",
}


def pytest_runtest_setup(item):
    os.environ["DATABASE_URL"] = PER_TEST[item.name]
    _root_gate(item)
"""

TWO_DB_TESTS = """
import pytest

@pytest.mark.db
def test_first():
    pass

@pytest.mark.db
def test_second():
    pass
"""


def test_the_db_verdict_is_cached_per_dsn_not_per_session(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLM_REQUIRE_DB", raising=False)
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    pytester.makeconftest(ROOT_CONFTEST.read_text() + PER_TEST_DSN)
    pytester.makepyfile(test_suite=TWO_DB_TESTS)

    result = pytester.runpytest("-rs")

    result.assert_outcomes(skipped=2)
    skips = [line for line in result.outlines if line.startswith("SKIPPED")]
    assert len(skips) == 2, skips
    # The second test changed the DSN, so it must get its own verdict, not the first one's.
    assert "port=9" in skips[0]
    assert "port=10" in skips[1]
