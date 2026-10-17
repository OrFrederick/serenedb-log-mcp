"""`scripts/replay_ledger.py <ledger.jsonl> [--answers <file>]`: exit codes and output, without a database.

The engine is tested in test_replay.py; here the database is the fake of tests/support/replay_fake.py.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import replay_fake
import yaml

import replay_ledger


@pytest.fixture(autouse=True)
def sample_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The script replays with the sample tool and the fake database of replay_fake."""
    real = replay_ledger.replay

    def with_sample(records: Any, investigation: Any = None, **_: Any) -> Any:
        return real(
            records, investigation, server_factory=replay_fake.sample_server, connect=replay_fake.fake_connect
        )

    monkeypatch.setattr(replay_ledger, "replay", with_sample)
    monkeypatch.setattr(replay_ledger, "database_problem", lambda: None)
    with replay_fake.fake_database():
        yield


@pytest.fixture
def ledger_file(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in replay_fake.record()), encoding="utf-8")
    return path


def answers_file(tmp_path: Path, answer: str) -> Path:
    path = tmp_path / "answers.yaml"
    path.write_text(
        yaml.safe_dump({"title": "t", "answers": [{"role": "engineer", "question": "q", "answer": answer}]}),
        encoding="utf-8",
    )
    return path


def test_a_ledger_that_holds_exits_0(
    ledger_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = replay_ledger.main([str(ledger_file), "--answers", str(answers_file(tmp_path, replay_fake.GOOD))])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "E1 sample_total: ok" in out
    assert "1 of 1 statements re-run as stored" in out
    assert "Replay passed" in out


def test_a_tampered_answer_exits_1(
    ledger_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tampered = replay_fake.GOOD.replace("152,734", "152,743")

    code = replay_ledger.main([str(ledger_file), "--answers", str(answers_file(tmp_path, tampered))])

    out = capsys.readouterr().out
    assert code == 1
    assert "number_not_in_evidence" in out
    assert "Replay failed" in out


def test_a_tampered_total_exits_1(ledger_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record = json.loads(ledger_file.read_text(encoding="utf-8"))
    record["total_matches"] = 5
    ledger_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

    code = replay_ledger.main([str(ledger_file)])

    assert code == 1
    assert "total_changed" in capsys.readouterr().out


@pytest.mark.parametrize("content", [None, "", "not json\n"])
def test_an_unusable_ledger_exits_2(
    tmp_path: Path, content: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.jsonl"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    assert replay_ledger.main([str(path)]) == 2
    assert "replay_ledger:" in capsys.readouterr().err


def test_an_unreachable_database_exits_2(
    ledger_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(replay_ledger, "database_problem", lambda: "SereneDB is not reachable")

    assert replay_ledger.main([str(ledger_file)]) == 2
    assert "not reachable" in capsys.readouterr().err
