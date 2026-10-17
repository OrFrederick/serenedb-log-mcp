"""`scripts/demo_replay.py`: the recorded demo, replayed offline without SereneDB (T24).

The ledger and the answers are the fixed investigation of tests/support/export_fixture.py. Every
test runs with a database connector that fails the test, so a replay that opened a connection would
show up here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from export_fixture import TEAM_LEAD_ANSWER, TITLE, answers, records

import demo_replay
from serenedb_log_mcp import db

BEATS = {
    "title": "t",
    "budget_s": 300,
    "beats": [
        {
            "title": "Lines",
            "role": "engineer",
            "seconds": 30,
            "prompt": "Show me the lines.",
            "expected": "A sample and the exact total.",
            "calls": [
                {"tool": "search_logs", "arguments": {"dataset": "bgl", "query": "data TLB error"}},
                {"tool": "get_context", "arguments": {"dataset": "bgl", "entry_id": "$entry_id"}},
            ],
        },
        {
            "title": "Counts",
            "role": "team_lead",
            "seconds": 30,
            "prompt": "Which categories recur?",
            "expected": "KERNDTLB on top.",
            "calls": [
                {"tool": "count_by", "arguments": {"group_by": "alert_category"}},
                {"tool": "latency_stats", "arguments": {"metric": "spawn"}},
            ],
        },
    ],
}


@pytest.fixture(autouse=True)
def no_database() -> Iterator[None]:
    def refuse(dsn: str, *, client_binding: bool) -> Any:
        pytest.fail("demo_replay opened a database connection")

    previous = db.set_connector(refuse)
    try:
        yield
    finally:
        db.set_connector(previous)


def write_ledger(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def ledger_file(tmp_path: Path) -> Path:
    return write_ledger(tmp_path / "ledger.jsonl", records())


@pytest.fixture
def beats_file(tmp_path: Path) -> Path:
    path = tmp_path / "beats.yaml"
    path.write_text(yaml.safe_dump(BEATS), encoding="utf-8")
    return path


def test_without_beats_it_prints_every_record(ledger_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert demo_replay.main([str(ledger_file)]) == 0

    out = capsys.readouterr().out
    assert "Replaying 6 Evidence records" in out
    assert "[E1] search_logs(role=engineer, dataset=bgl, query=data TLB error" in out
    assert "total_matches 152,734, returned 12, truncated: yes, 412.5 ms in SereneDB" in out
    assert "(first 5 of 12 returned rows)" in out
    assert "[E6] count_by(role=engineer" in out


def test_with_beats_it_groups_the_records_by_beat(
    ledger_file: Path, beats_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo_replay.main([str(ledger_file), "--beats", str(beats_file)]) == 0

    out = capsys.readouterr().out
    first, second = out.index("== Beat 1: Lines (30 s, engineer)"), out.index("== Beat 2: Counts")
    assert first < out.index("[E1] search_logs") < out.index("[E2] get_context") < second
    assert "Prompt: Which categories recur?" in out
    assert second < out.index("[E3] count_by") < out.index("not in the ledger: latency_stats")
    leftover = out.index("== Evidence outside the beats (3 records)")
    assert leftover < out.index("[E4] trend") < out.index("[E5] count_by") < out.index("[E6] count_by")


def test_one_beat_shows_only_that_beat(
    ledger_file: Path, beats_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo_replay.main([str(ledger_file), "--beats", str(beats_file), "--beat", "2"]) == 0

    out = capsys.readouterr().out
    assert "== Beat 2: Counts" in out
    assert "Beat 1" not in out
    assert "outside the beats" not in out


def test_a_record_with_other_arguments_is_shown_in_its_beat_and_marked(
    tmp_path: Path, beats_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    search = {**records()[0], "arguments": {**records()[0]["arguments"], "query": "TLB"}}
    ledger_file = write_ledger(tmp_path / "live.jsonl", [search])

    assert demo_replay.main([str(ledger_file), "--beats", str(beats_file), "--beat", "1"]) == 0

    out = capsys.readouterr().out
    assert "[E1] search_logs" in out
    assert "(made with other arguments than the run sheet's call)" in out


def test_pause_waits_before_every_beat(ledger_file: Path, beats_file: Path) -> None:
    prompts: list[str] = []

    assert (
        demo_replay.main([str(ledger_file), "--beats", str(beats_file), "--pause"], wait=prompts.append) == 0
    )

    assert prompts == ["[Enter] beat 1: Lines ", "[Enter] beat 2: Counts "]


def test_answers_are_checked_offline_as_verify_answer_checks_them(
    ledger_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "answers.yaml"
    path.write_text(
        yaml.safe_dump({"title": TITLE, "answers": [a.model_dump(mode="json") for a in answers()]}),
        encoding="utf-8",
    )

    assert demo_replay.main([str(ledger_file), "--answers", str(path)]) == 0

    out = capsys.readouterr().out
    assert f"== Answers: {TITLE}" in out
    assert "2. team_lead: Which alert categories recur?" in out
    assert out.count("verify_answer: OK") == 3


def test_a_rounded_draft_fails_with_the_hint(
    ledger_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(
        TEAM_LEAD_ANSWER.replace("152,734 Log Entries", "about 150k Log Entries"), encoding="utf-8"
    )

    assert demo_replay.main([str(ledger_file), "--draft", str(draft), "--role", "team_lead"]) == 1

    out = capsys.readouterr().out
    assert "verify_answer: 1 problem(s)" in out
    assert "rounded_number in Observed Facts" in out
    assert "152734" in out


def test_the_fixed_draft_passes(
    ledger_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(TEAM_LEAD_ANSWER, encoding="utf-8")

    assert demo_replay.main([str(ledger_file), "--draft", str(draft), "--role", "team_lead"]) == 0
    assert "verify_answer: OK" in capsys.readouterr().out


@pytest.mark.parametrize(("role", "shown"), [("manager", 0), ("team_lead", 3), ("engineer", 5)])
def test_rows_are_shown_as_the_role_received_them(
    tmp_path: Path, role: str, shown: int, capsys: pytest.CaptureFixture[str]
) -> None:
    search = {**records()[0], "role": role}

    assert demo_replay.main([str(write_ledger(tmp_path / "l.jsonl", [search]))]) == 0

    out = capsys.readouterr().out
    assert out.count("synthetic **data TLB error**") == shown
    assert ("rows withheld" in out) == (role == "manager")


@pytest.mark.parametrize("content", [None, "", "not json\n"])
def test_an_unusable_ledger_exits_2(
    tmp_path: Path, content: str | None, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "ledger.jsonl"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    assert demo_replay.main([str(path)]) == 2
    assert "demo_replay:" in capsys.readouterr().err


def test_a_beat_out_of_range_exits_2(
    ledger_file: Path, beats_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo_replay.main([str(ledger_file), "--beats", str(beats_file), "--beat", "3"]) == 2
    assert "has beats 1 to 2" in capsys.readouterr().err


def test_a_missing_draft_exits_2(ledger_file: Path, tmp_path: Path) -> None:
    assert demo_replay.main([str(ledger_file), "--draft", str(tmp_path / "no.md"), "--role", "manager"]) == 2


@pytest.mark.parametrize("argv", [["--draft", "d.md"], ["--role", "manager"], ["--beat", "1"]])
def test_options_that_go_together_are_checked(ledger_file: Path, argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        demo_replay.main([str(ledger_file), *argv])
    assert exc.value.code == 2
