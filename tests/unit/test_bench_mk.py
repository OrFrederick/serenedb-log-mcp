"""The benchmark targets in mk/bench.mk are included by the root Makefile and listed by `make help`."""

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


def test_help_lists_bench_index() -> None:
    assert "bench-index" in make("help")


def test_bench_index_runs_the_trade_off_benchmark_with_its_arguments() -> None:
    out = make("-n", "bench-index", "BENCH_ARGS=--runs 5")

    assert "bench/index_tradeoff.py --runs 5" in out
