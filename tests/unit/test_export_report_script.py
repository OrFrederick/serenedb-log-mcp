"""`scripts/export_report.py <ledger.jsonl> <answers.yaml> --role <role> --out <file>`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from export_fixture import TITLE, answers, records

import export_report
from serenedb_log_mcp import config
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export import render_investigation


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in records()), encoding="utf-8")
    answers_file = tmp_path / "answers.yaml"
    answers_file.write_text(
        yaml.safe_dump({"title": TITLE, "answers": [a.model_dump(mode="json") for a in answers()]}),
        encoding="utf-8",
    )
    return ledger, answers_file


def test_it_writes_the_markdown_the_tool_returns(files: tuple[Path, Path], tmp_path: Path) -> None:
    ledger, answers_file = files
    out = tmp_path / "report.md"
    before = ledger.read_bytes()

    code = export_report.main([str(ledger), str(answers_file), "--role", "engineer", "--out", str(out)])

    assert code == 0
    # The product server offers list_incidents and top_patterns (T27, T28), so the side-by-side check runs.
    expected = render_investigation(Role.ENGINEER, TITLE, answers(), records(), incident_tools=True)
    assert out.read_text(encoding="utf-8") == expected.markdown
    assert ledger.read_bytes() == before


def test_the_role_is_the_audience(files: tuple[Path, Path], tmp_path: Path) -> None:
    ledger, answers_file = files
    out = tmp_path / "manager.md"

    assert export_report.main([str(ledger), str(answers_file), "--role", "manager", "--out", str(out)]) == 0

    assert "Rows withheld for the manager audience" in out.read_text(encoding="utf-8")


def test_without_out_it_writes_into_the_export_dir(files: tuple[Path, Path], tmp_path: Path) -> None:
    ledger, answers_file = files
    config.set_settings(config.load_settings({}, export_dir=tmp_path / "exports"))

    assert export_report.main([str(ledger), str(answers_file), "--role", "engineer"]) == 0

    assert (tmp_path / "exports" / "kerndtlb-storm-on-one-node-engineer.md").exists()


def test_a_missing_ledger_is_a_usage_error(
    files: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, answers_file = files

    code = export_report.main([str(tmp_path / "nope.jsonl"), str(answers_file), "--role", "engineer"])

    assert code == 2
    assert "nope.jsonl" in capsys.readouterr().err


def test_a_bad_answers_file_is_a_usage_error(
    files: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, _ = files
    bad = tmp_path / "bad.yaml"
    bad.write_text("title: x\nanswers: []\n", encoding="utf-8")

    code = export_report.main([str(ledger), str(bad), "--role", "engineer", "--out", str(tmp_path / "r.md")])

    assert code == 2
    assert "answers" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()
