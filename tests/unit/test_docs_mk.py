"""The docs target (T8) in mk/docs.mk."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def make(*args: str) -> str:
    proc = subprocess.run(["make", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
    return proc.stdout


def test_help_lists_the_docs_target() -> None:
    assert "docs" in make("help").split()


def test_docs_runs_the_generator() -> None:
    assert "scripts/gen_tool_docs.py" in make("-n", "docs")
