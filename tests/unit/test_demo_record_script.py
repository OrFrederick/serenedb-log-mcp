"""`scripts/demo_record.py`: the timed dry run of the demo's tool calls, without a real database (T24).

The calls go to the sample tool of tests/support/replay_fake.py on the fake database, so the ledger,
the timings and the exit codes are tested here; tests/db/test_demo_db.py runs the real beats.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import replay_fake
import yaml

import demo_record
from serenedb_log_mcp import config
from serenedb_log_mcp.ledger import Ledger


def beats(*keys: str) -> dict[str, Any]:
    return {
        "title": "t",
        "budget_s": 300,
        "beats": [
            {"title": "Opening", "seconds": 10, "prompt": "p", "expected": "e"},
            {
                "title": "Counts",
                "role": "engineer",
                "seconds": 40,
                "prompt": "p",
                "expected": "e",
                "calls": [{"tool": "sample_total", "arguments": {"key": key}} for key in keys],
            },
        ],
    }


@pytest.fixture(autouse=True)
def sample_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    real = demo_record.record
    monkeypatch.setattr(demo_record, "record", lambda b: real(b, server_factory=replay_fake.sample_server))
    monkeypatch.setattr(demo_record, "database_problem", lambda: None)
    monkeypatch.delenv("SLM_QUERY_TIMEOUT_S", raising=False)
    with replay_fake.fake_database():
        yield


def beats_file(tmp_path: Path, *keys: str) -> Path:
    path = tmp_path / "beats.yaml"
    path.write_text(yaml.safe_dump(beats(*keys)), encoding="utf-8")
    return path


def test_it_records_every_call_into_a_fresh_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "out" / "ledger.jsonl"
    before = config.get_settings()

    code = demo_record.main(
        ["--beats", str(beats_file(tmp_path, "KERNDTLB", "KERNSTOR")), "--ledger", str(ledger)]
    )

    out = capsys.readouterr().out
    assert code == 0, out
    recorded = Ledger(ledger).all()
    assert [r["evidence_id"] for r in recorded] == ["E1", "E2"]
    assert [r["arguments"]["key"] for r in recorded] == ["KERNDTLB", "KERNSTOR"]
    assert "SLM_QUERY_TIMEOUT_S 60" in out
    assert "Opening: no tool call recorded (slot 10 s)" in out
    assert "Counts: 2 call(s)" in out
    assert "E1 sample_total as engineer:" in out
    assert "Recorded 2 of 2 calls" in out
    assert "the run sheet plans 50 s of 300 s" in out
    assert config.get_settings() == before


def test_the_environment_timeout_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SLM_QUERY_TIMEOUT_S", "90")
    monkeypatch.setattr(config, "_active", config.load_settings())

    assert (
        demo_record.main(["--beats", str(beats_file(tmp_path, "KERNDTLB")), "--ledger", str(tmp_path / "l")])
        == 0
    )
    assert "SLM_QUERY_TIMEOUT_S 90" in capsys.readouterr().out


def test_a_failing_call_exits_1_and_the_others_still_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"

    code = demo_record.main(
        ["--beats", str(beats_file(tmp_path, "boom", "KERNDTLB")), "--ledger", str(ledger)]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "FAILED sample_total as engineer" in out
    assert "Recorded 1 of 2 calls" in out
    assert len(Ledger(ledger).all()) == 1


def test_an_existing_ledger_needs_replace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text('{"evidence_id": "E1"}\n', encoding="utf-8")
    beats_path = beats_file(tmp_path, "KERNDTLB")

    assert demo_record.main(["--beats", str(beats_path), "--ledger", str(ledger)]) == 2
    assert "--replace" in capsys.readouterr().err

    assert demo_record.main(["--beats", str(beats_path), "--ledger", str(ledger), "--replace"]) == 0
    assert [r["evidence_id"] for r in Ledger(ledger).all()] == ["E1"]
    assert Ledger(ledger).all()[0]["tool"] == "sample_total"


def test_an_unreachable_database_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(demo_record, "database_problem", lambda: "SereneDB is not reachable")

    assert (
        demo_record.main(["--beats", str(beats_file(tmp_path, "KERNDTLB")), "--ledger", str(tmp_path / "l")])
        == 2
    )
    assert "not reachable" in capsys.readouterr().err


def test_an_unusable_beats_file_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_record.main(["--beats", str(tmp_path / "none.yaml"), "--ledger", str(tmp_path / "l")]) == 2
    assert "demo_record: cannot read" in capsys.readouterr().err
