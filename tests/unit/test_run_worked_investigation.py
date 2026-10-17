"""`scripts/run_worked_investigation.py`: the T18 driver, without a model (a fake `claude`)."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
import yaml
from export_fixture import answers, records

import run_worked_investigation as wi
from serenedb_log_mcp.evidence import Role

FAKE_ANSWER = (
    "Here is the verified answer.\n\n"
    "## Observed Facts\n- 152,734 Log Entries matched [E1].\n\n"
    "## Hypotheses\n- None.\n\n## Unknowns\n- None.\n\n## Next Step\n- Check E1 again.\n"
)


def _fake_claude(tmp_path: Path) -> Path:
    """A `claude` that records its stdin and arguments and answers with FAKE_ANSWER."""
    script = tmp_path / "claude"
    log = tmp_path / "calls.jsonl"
    events = [
        {
            "type": "system",
            "subtype": "init",
            "mcp_servers": [{"name": "serenedb-log", "status": "connected"}],
        },
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": "t1", "name": "mcp__serenedb-log__search_logs"}]
            },
        },
        {"type": "result", "result": "What would you like to know?", "is_error": False},
        {"type": "result", "result": FAKE_ANSWER, "is_error": False, "total_cost_usd": 0.5, "num_turns": 3},
    ]
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"with open({str(log)!r}, 'a') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}) + '\\n')\n"
        + "".join(f"print({json.dumps(json.dumps(e))})\n" for e in events),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def test_the_three_roles_run_in_order_with_their_role_prompts(capsys: pytest.CaptureFixture[str]) -> None:
    assert [q.role for q in wi.QUESTIONS] == [Role.ENGINEER, Role.TEAM_LEAD, Role.MANAGER]

    assert wi.main(["--dry-run", "--out", "unused"]) == 0

    out = capsys.readouterr().out
    commands = [line for line in out.splitlines() if line.startswith("claude -p")]
    assert len(commands) == 3
    assert all("--strict-mcp-config" in c and "--model claude-opus-5-5" in c for c in commands)
    prompts = [
        json.loads(line.split("stdin: ", 1)[1])["message"]["content"]
        for line in out.splitlines()
        if "stdin" in line
    ]
    assert prompts[0::2] == [
        "/mcp__serenedb-log__engineer bgl KERNMNTF",
        "/mcp__serenedb-log__team_lead both",
        "/mcp__serenedb-log__manager bgl",
    ]


def test_the_questions_carry_the_content_of_issue_5() -> None:
    engineer, team_lead, manager = (q.question for q in wi.QUESTIONS)
    assert "/p/gb1" in engineer
    assert "full-text search" in engineer
    assert all(uuid in team_lead for uuid in wi.SLOW_SPAWNS)
    assert "whole Base Corpus" in team_lead
    assert "by Team" in team_lead
    assert "The largest KERNDTLB Incident has <N> Log Entries on <N> node (<date>)." in manager
    assert "two Data Scopes" in manager
    assert "band" in manager
    assert "downtime and money" in manager


def test_a_later_role_is_told_which_evidence_the_earlier_answers_cite() -> None:
    earlier = [
        wi.Session(role="engineer", question="q", answer="x [E1] y [E3] and [E1] again"),
        wi.Session(role="team_lead", question="q", answer="no citations"),
    ]

    text = wi.handover(earlier)

    assert "the engineer answer cites E1, E3" in text
    assert "the team_lead answer cites none" in text
    assert "get_evidence" in text
    assert wi.handover([]) == ""


def test_the_answer_starts_at_observed_facts() -> None:
    assert wi.answer_only(FAKE_ANSWER).startswith("## Observed Facts\n")
    assert wi.answer_only("no headings at all") == "no headings at all\n"


def test_an_alias_is_not_a_pinned_model(capsys: pytest.CaptureFixture[str]) -> None:
    assert wi.main(["--model", "opus", "--dry-run"]) == 2
    assert "pinned model id" in capsys.readouterr().err


def test_a_run_drives_claude_with_one_shared_ledger_and_writes_the_answers(tmp_path: Path) -> None:
    claude = _fake_claude(tmp_path)
    out = tmp_path / "wi"
    environ = {"DATABASE_URL": "host=db", "SLM_SCHEMA": "slm", "CLAUDECODE": "1", "CLAUDE_CODE_X": "1"}
    options = wi.Options(out=out, report=tmp_path / "report.md", claude_bin=str(claude))

    code = wi.run(options, environ)

    # The fake writes no Evidence, so its answers cite E1 that is not there and the export has no ledger.
    assert code == 2
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 3
    messages = [[json.loads(m)["message"]["content"] for m in c["stdin"].splitlines()] for c in calls]
    assert messages[0][0] == "/mcp__serenedb-log__engineer bgl KERNMNTF"
    assert "the engineer answer cites E1" in messages[1][1]
    assert "the engineer answer cites E1; the team_lead answer cites E1" in messages[2][1]
    assert all(wi.FINAL_MESSAGE in m[1] for m in messages)
    saved = yaml.safe_load((out / "answers.yaml").read_text())
    assert saved["title"] == wi.TITLE
    assert [a["role"] for a in saved["answers"]] == ["engineer", "team_lead", "manager"]
    assert all(a["answer"].startswith("## Observed Facts") for a in saved["answers"])
    run = json.loads((out / "run.json").read_text())
    assert [s["tool_calls"] for s in run["sessions"]] == [["search_logs"]] * 3
    assert all(s["cost_usd"] == 0.5 and not s["check"]["ok"] for s in run["sessions"])
    assert not list(out.glob("*.mcp.json"))
    assert sorted(p.name for p in out.glob("*.stream.jsonl")) == [
        "1-engineer.stream.jsonl",
        "2-team_lead.stream.jsonl",
        "3-manager.stream.jsonl",
    ]


def test_the_server_gets_the_shared_ledger_and_claude_a_clean_environment() -> None:
    config = wi.mcp_config(Path("/x/ledger.jsonl"), ["py", "-m", "srv"], {"SLM_SCHEMA": "s", "HOME": "/h"})

    server = config["mcpServers"]["serenedb-log"]
    assert server["env"] == {"SLM_SCHEMA": "s", "LEDGER_PATH": "/x/ledger.jsonl"}
    assert wi.clean_env({"CLAUDECODE": "1", "CLAUDE_CODE_SSE": "1", "PATH": "/bin"}) == {"PATH": "/bin"}


def test_export_only_renders_the_report_from_the_ledger_and_answers(tmp_path: Path) -> None:
    out = tmp_path / "wi"
    out.mkdir()
    (out / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records()), encoding="utf-8")
    wi.write_answers(
        out / "answers.yaml",
        [wi.Session(role=a.role.value, question=a.question, answer=a.answer) for a in answers()],
    )
    report = tmp_path / "report.md"

    code = wi.main(["--export-only", "--out", str(out), "--report", str(report)])

    assert code in (0, 1)  # 1 only when a fixture answer has verify_answer problems
    assert report.read_text(encoding="utf-8").startswith(f"# {wi.TITLE}\n")


def test_export_only_without_a_ledger_is_a_usage_error(tmp_path: Path) -> None:
    assert wi.main(["--export-only", "--out", str(tmp_path)]) == 2


def _stream(*results: str) -> str:
    return "\n".join(json.dumps({"type": "result", "result": r, "is_error": False}) for r in results)


def test_a_session_that_answers_only_the_role_prompt_is_an_error() -> None:
    answer, _, _, _, error = wi.parse_stream(_stream("What would you like to know?"))

    assert answer == ""
    assert error == "the session ended before it answered the question"


def test_the_answer_is_the_last_result() -> None:
    answer, _, _, _, error = wi.parse_stream(_stream("What would you like to know?", FAKE_ANSWER))

    assert error == ""
    assert answer.startswith("## Observed Facts")


def test_the_committed_report_is_the_export_of_the_committed_ledger_and_answers(tmp_path: Path) -> None:
    """docs/worked-investigation.md is reproducible without a model or a database."""
    out = tmp_path / "report.md"

    assert wi.main(["--export-only", "--report", str(out)]) == 0

    assert out.read_text(encoding="utf-8") == wi.DEFAULT_REPORT.read_text(encoding="utf-8")
