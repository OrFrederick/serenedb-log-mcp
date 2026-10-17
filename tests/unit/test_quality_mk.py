"""The quality targets in mk/quality.mk are included by the root Makefile and listed by `make help`."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def make(*args: str) -> str:
    proc = subprocess.run(["make", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, f"make {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def test_help_lists_the_golden_runner() -> None:
    assert "test-golden" in make("help")


def test_the_golden_runner_runs_every_corpus_test_and_never_skips_one() -> None:
    out = make("-n", "test-golden")

    assert "pytest -m corpus" in out
    # A missing Base Corpus is a failure here, not a skip (tests/conftest.py).
    assert "SLM_REQUIRE_DB=1" in out
