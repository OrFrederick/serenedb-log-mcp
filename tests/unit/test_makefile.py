"""Makefile targets that cannot run in tests are checked with `make -n` (print, do not run)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def dry_run(*args: str) -> str:
    proc = subprocess.run(
        ["make", "-n", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=True
    )
    return proc.stdout


def test_db_up_pins_the_amd64_image_platform() -> None:
    # serenedb/serenedb:26.10.1 is published for linux/amd64 only.
    out = dry_run("db-up")

    assert "--platform linux/amd64" in out
    assert "-e SERENEDB_HOST_AUTH_METHOD=trust" in out
    assert "serenedb/serenedb:26.10.1" in out


def test_db_up_platform_can_be_overridden() -> None:
    assert "--platform linux/arm64" in dry_run("db-up", "SERENEDB_PLATFORM=linux/arm64")
