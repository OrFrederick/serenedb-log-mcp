"""Log files are ignored only under data/, so small committed log fixtures stay visible to git."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout"
)


def ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False).returncode == 0


def test_corpus_logs_under_data_are_ignored() -> None:
    assert ignored("data/raw/BGL.log")
    assert ignored("data/sample/OpenStack_2k.log")


def test_log_fixtures_outside_data_are_not_ignored() -> None:
    assert not ignored("tests/fixtures/golden.log")
