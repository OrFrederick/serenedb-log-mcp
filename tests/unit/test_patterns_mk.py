"""The Pattern scoring target (T9) in mk/data.mk."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def make(*args: str) -> str:
    proc = subprocess.run(["make", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    return proc.stdout


def test_help_lists_the_score_patterns_target() -> None:
    assert "score-patterns" in make("help")


def test_score_patterns_runs_the_scorer() -> None:
    assert "scripts/score_patterns.py" in make("-n", "score-patterns")
