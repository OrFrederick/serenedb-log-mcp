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


REMOTE_SUITE = """
import pytest

pytestmark = [pytest.mark.db, pytest.mark.remote]

def test_reads_s3():
    pass
"""


def test_remote_tests_skip_unless_asked_for_even_when_the_db_is_required(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Remote tests read the public S3 text_bench file from the SereneDB server: slow, and outside our
    # control, so they never run by accident, also not under SLM_REQUIRE_DB=1 (`make test-remote`).
    monkeypatch.setenv("SLM_REQUIRE_DB", "1")
    monkeypatch.delenv("SLM_REMOTE_TESTS", raising=False)
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE)
    pytester.makeconftest(ROOT_CONFTEST.read_text())
    pytester.makepyfile(test_remote=REMOTE_SUITE)

    result = pytester.runpytest("-rs", "--strict-markers")

    result.assert_outcomes(skipped=1)
    assert any("SLM_REMOTE_TESTS=1" in line for line in result.outlines)


def test_the_remote_marker_is_registered(suite: pytest.Pytester) -> None:
    suite.runpytest("--markers").stdout.fnmatch_lines(["@pytest.mark.remote:*"])
