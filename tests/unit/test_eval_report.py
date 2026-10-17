"""The eval report (T17): grading a run directory, pass^k and Wilson CIs per question, result sections."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from eval_trials import make_trial

from graders import Grade
from questions import load
from report import Graded, grade_run, main, per_question, render, write_section
from transcript import Trial

QS = load()
RETRY = "Error executing tool count_by: x. Retry the call once; if it fails again, narrow the Data Scope"


def trials() -> list[Trial]:
    """tl-03: 2 of 3 trials pass (one calls a forbidden tool); tl-04: 1 pass, 1 infra; mgr-01 ... (triple)."""
    good_tl03 = [("trend", {"role": "team_lead", "bucket": "week", "levels": ["FATAL"]})]
    bad_tl03 = [*good_tl03, ("get_context", {"role": "team_lead", "entry_id": 1})]
    tl04 = [
        ("count_by", {"role": "team_lead", "group_by": "level"}),
        ("count_by", {"role": "team_lead", "group_by": "component"}),
    ]
    return [
        make_trial("tl-03", "team_lead", good_tl03, "answer", repeat=0),
        make_trial("tl-03", "team_lead", good_tl03, "answer", repeat=1),
        make_trial("tl-03", "team_lead", bad_tl03, "answer", repeat=2),
        make_trial("tl-04", "team_lead", tl04, "answer", repeat=0),
        make_trial("tl-04", "team_lead", tl04, "answer", repeat=1, errors={0: RETRY}),
        make_trial("eng-01", "engineer", [("search_logs", {"role": "engineer"})], "answer"),
        make_trial("tl-01", "team_lead", [("count_by", {"role": "team_lead", "group_by": "node"})], "answer"),
        make_trial("mgr-01", "manager", [("count_by", {"role": "manager", "group_by": "node"})], "answer"),
    ]


def graded_l1_only() -> list[Graded]:
    """Grade with L2 checks dropped, so the statistics follow the tool paths alone."""
    out = []
    for g in grade_run(trials(), QS, {}):
        kept = [c for c in g.grade.checks if c.layer != "L2"]
        out.append(Graded(g.trial, g.question, Grade(g.question.id, kept)))
    return out


def test_per_question_counts_leave_infra_trials_out() -> None:
    stats = {s.question.id: s for s in per_question(graded_l1_only(), k=3)}

    tl03, tl04 = stats["tl-03"], stats["tl-04"]
    assert (tl03.trials, tl03.graded, tl03.passed, tl03.infra) == (3, 3, 2, 0)
    assert tl03.pass_hat_k == 0.0
    assert tl03.failed_checks == {"l1.must_not_call": 1}
    assert (tl04.trials, tl04.graded, tl04.passed, tl04.infra) == (2, 1, 1, 1)
    assert tl04.pass_hat_k is None  # fewer graded trials than k


def test_render_has_the_tables_the_issue_asks_for() -> None:
    meta = {"model": "claude-haiku-5-5", "k": 3, "role_delivery": "slash"}

    body = render(
        graded_l1_only(),
        QS,
        meta,
        label="claude-haiku-5-5-k3",
        golden_ran=False,
        now=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=UTC),
    )

    assert body.startswith("## claude-haiku-5-5-k3\n")
    assert "graded 2026-10-18 12:00 UTC" in body
    assert "1 with an infra failure" in body
    tl03 = "| tl-03 | team_lead | holdout | 2/3 | 0 | 66.7% | [20.8%, 93.9%] | 0.0% | l1.must_not_call x1 |"
    assert tl03 in body
    assert "| tl-04 | team_lead | tune | 1/1 | 1 | 100.0% |" in body
    assert "### Per check" in body
    assert "| l1.must_not_call | L1 | yes | 4 | 5 | 80.0% |" in body
    assert "| kerndtlb | search_logs | count_by:node | count_by:node | 0.00 | 0.00 | 1.00 |" in body
    assert "| os503 | not run | not run | not run | - | - | - |" in body
    assert "**infra.tool_errors**" in body


def test_write_section_replaces_its_own_section_only(tmp_path: Path) -> None:
    path = tmp_path / "eval.md"
    path.write_text("# Eval results\n\n<!-- BEGIN pending -->\nNo run yet.\n<!-- END pending -->\n")

    write_section(path, "a-k3", "## a\nfirst\n")
    write_section(path, "b-k1", "## b\nother\n")
    write_section(path, "a-k3", "## a\nsecond\n")

    text = path.read_text()
    assert text.startswith("# Eval results")
    assert "second" in text
    assert "first" not in text
    assert "No run yet" not in text
    assert text.count("<!-- BEGIN a-k3 -->") == 1
    assert text.index("BEGIN a-k3") < text.index("BEGIN b-k1")


def test_main_grades_a_run_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "trials").mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"model": "claude-haiku-5-5", "k": 3}))
    for trial in trials():
        name = f"{trial.question_id}__t{trial.repeat}.json"
        (run_dir / "trials" / name).write_text(json.dumps(trial.to_json()))
    results = tmp_path / "eval.md"

    code = main([str(run_dir), "--no-golden", "--results", str(results)])

    assert code == 0, capsys.readouterr().err
    assert (run_dir / "report.md").read_text() in results.read_text()
    rows = [json.loads(line) for line in (run_dir / "grades.jsonl").read_text().splitlines()]
    assert len(rows) == 8
    assert {r["verdict"] for r in rows} <= {"pass", "fail", "infra"}
    assert "<!-- BEGIN claude-haiku-5-5-k3 -->" in results.read_text()


def test_main_without_trials_is_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "run.json").write_text("{}")

    assert main([str(tmp_path), "--no-golden"]) == 2
    assert "no trials" in capsys.readouterr().err
