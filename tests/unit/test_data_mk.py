"""The data targets in mk/data.mk are included by the root Makefile and listed by `make help`."""

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


def test_help_lists_the_included_data_targets() -> None:
    out = make("help")

    assert "fetch-data" in out
    assert "derive" in out
    # T1's own targets are still listed.
    assert "test-corpus" in out


def test_fetch_data_runs_the_fetch_script() -> None:
    assert "scripts/fetch_data.py" in make("-n", "fetch-data")


def test_derive_runs_the_dev_adapter_by_default() -> None:
    out = make("-n", "derive")

    assert "scripts/derive.py" in out
    assert "--adapter dev" in out


def test_derive_adapter_can_be_switched_to_official() -> None:
    assert "--adapter official" in make("-n", "derive", "ADAPTER=official")
