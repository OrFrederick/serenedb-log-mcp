"""The demo configuration (T24): the make targets in mk/demo.mk and docs/demo/mcp.demo.json."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import demo_record
from serenedb_log_mcp.config import Settings

ROOT = Path(__file__).parents[2]
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def make(*args: str) -> str:
    proc = subprocess.run(["make", *args], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, f"make {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


@needs_make
def test_help_lists_the_demo_targets() -> None:
    out = make("help")

    assert "demo-record" in out
    assert "demo-replay" in out


@needs_make
def test_demo_record_writes_a_fresh_ledger_from_the_beats() -> None:
    out = make("-n", "demo-record")

    assert (
        "scripts/demo_record.py --beats docs/demo/beats.yaml --ledger exports/demo-ledger.jsonl --replace"
        in out
    )


@needs_make
def test_demo_replay_reads_that_ledger_and_passes_extra_arguments() -> None:
    out = make("-n", "demo-replay", "REPLAY_ARGS=--beat 2")

    assert "scripts/demo_replay.py exports/demo-ledger.jsonl --beats docs/demo/beats.yaml --beat 2" in out


def test_the_demo_mcp_config_raises_the_query_timeout_and_keeps_its_own_ledger() -> None:
    servers = json.loads((ROOT / "docs" / "demo" / "mcp.demo.json").read_text(encoding="utf-8"))["mcpServers"]
    project = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    server = servers["serenedb-log"]
    env = server.pop("env")

    assert server == project["serenedb-log"]
    assert float(env["SLM_QUERY_TIMEOUT_S"]) == demo_record.DEMO_QUERY_TIMEOUT_S > Settings().query_timeout_s
    assert env["LEDGER_PATH"].startswith("exports/")
    assert env["LEDGER_PATH"] != "exports/demo-ledger.jsonl"
