"""The demo's beats against SereneDB (T24): `demo_record.py` runs every call of docs/demo/beats.yaml on
the derived Loghub samples, and `demo_replay.py` maps the recorded ledger back to every beat offline."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pytest

import demo_record
import demo_replay
from serenedb_log_mcp import config, ledger
from serenedb_log_mcp.config import load_settings
from serenedb_log_mcp.corpus import facts


class Schemas(Protocol):
    raw: str
    slm: str


def test_the_demo_records_and_replays_every_beat(
    derived_sample: Schemas,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(config, "_active", load_settings(slm_schema=derived_sample.slm))
    monkeypatch.setattr(ledger, "_active", None)
    facts.clear_cache()
    path = tmp_path / "demo-ledger.jsonl"

    code = demo_record.main(["--ledger", str(path)])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "FAILED" not in out
    assert demo_replay.main([str(path), "--beats", str(demo_record.DEFAULT_BEATS)]) == 0
    replayed = capsys.readouterr().out
    assert "not in the ledger" not in replayed
    assert "outside the beats" not in replayed
    facts.clear_cache()
