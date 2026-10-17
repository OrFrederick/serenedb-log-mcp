"""The eval driver (T17), run against a fake `claude` executable: no model call, no cost."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from driver import (
    DriverError,
    Options,
    claude_command,
    clean_env,
    main,
    mcp_config,
    pinned_model,
    run,
    user_messages,
)
from questions import load
from transcript import Trial

QS = load()

FAKE_CLAUDE = """\
#!{python}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
server = config["mcpServers"]["serenedb-log"]
Path(server["env"]["LEDGER_PATH"]).write_text(json.dumps({{"evidence_id": "E1", "tool": "count_by"}}) + "\\n")
record = {{
    "argv": args,
    "stdin": sys.stdin.read(),
    "cwd": os.getcwd(),
    "leaked": sorted(k for k in os.environ if k.startswith("CLAUDE_CODE_") or k == "CLAUDECODE"),
}}
Path(os.environ["FAKE_CLAUDE_LOG"]).write_text(json.dumps(record))
events = [
    {{"type": "system", "subtype": "init", "model": "claude-haiku-5-5",
      "mcp_servers": [{{"name": "serenedb-log", "status": "connected"}}],
      "tools": ["mcp__serenedb-log__count_by"]}},
    {{"type": "result", "subtype": "success", "is_error": False, "result": "Ask me.",
      "total_cost_usd": 0.001}},
    {{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "t1",
      "name": "mcp__serenedb-log__count_by", "input": {{"role": "engineer", "group_by": "node"}}}}]}}}},
    {{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1",
      "content": [{{"type": "text", "text": "ok"}}]}}]}}}},
    {{"type": "result", "subtype": "success", "is_error": False, "result": "## Observed Facts",
      "total_cost_usd": 0.004, "num_turns": 4, "duration_ms": 900}},
]
print("\\n".join(json.dumps(e) for e in events))
"""


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir()
    path.write_text(FAKE_CLAUDE.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "fake.json"))
    return path


def options(tmp_path: Path, claude: Path | str = "claude", **overrides: object) -> Options:
    values: dict[str, object] = {"model": "claude-haiku-5-5", "k": 1, "out": tmp_path / "run"}
    values["claude_bin"] = str(claude)
    return Options(**(values | overrides))  # type: ignore[arg-type]


def test_aliases_are_refused_and_pinned_ids_kept() -> None:
    assert pinned_model("claude-haiku-5-5") == "claude-haiku-5-5"
    assert pinned_model("claude-sonnet-4-5-20250929") == "claude-sonnet-4-5-20250929"
    for alias in ("haiku", "sonnet", "opus", "claude-haiku"):
        with pytest.raises(DriverError, match="not a pinned model id"):
            pinned_model(alias)


def test_clean_env_strips_the_parent_claude_code_session() -> None:
    env = clean_env({"CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli", "PATH": "/bin", "CLAUDE_X": "kept"})

    assert env == {"PATH": "/bin", "CLAUDE_X": "kept"}


def test_the_command_isolates_the_run_from_the_developers_claude_code(tmp_path: Path) -> None:
    cmd = claude_command(options(tmp_path), QS.by_id()["eng-01"], tmp_path / "c.json")

    joined = " ".join(cmd)
    for flag in ("-p", "--strict-mcp-config", "--verbose", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__serenedb-log__*"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-5-5"
    assert "--max-budget-usd 0.75" in joined
    assert "--append-system-prompt" not in cmd
    assert "--bare" not in cmd


def test_system_delivery_appends_the_rendered_role_prompt(tmp_path: Path) -> None:
    question = QS.by_id()["mgr-01"]

    cmd = claude_command(options(tmp_path, role_delivery="system"), question, tmp_path / "c.json")
    messages = user_messages(options(tmp_path, role_delivery="system"), question)

    prompt = cmd[cmd.index("--append-system-prompt") + 1]
    assert "I am the Manager" in prompt
    assert "Use dataset='bgl'." in prompt
    assert len(messages) == 1


def test_slash_delivery_sends_the_role_prompt_then_the_question(tmp_path: Path) -> None:
    question = QS.by_id()["eng-01"]

    messages = [json.loads(m) for m in user_messages(options(tmp_path), question)]

    assert [m["message"]["content"] for m in messages] == [
        "/mcp__serenedb-log__engineer bgl",
        question.question,
    ]
    assert all(m["type"] == "user" for m in messages)


def test_mcp_config_gives_the_server_its_own_ledger() -> None:
    environ = {"DATABASE_URL": "host=h", "HOME": "/h"}
    config = mcp_config(Path("/r/t.ledger.jsonl"), ("py", "-m", "x"), environ)

    server = config["mcpServers"]["serenedb-log"]
    assert server["command"] == "py"
    assert server["args"] == ["-m", "x"]
    assert server["env"] == {"DATABASE_URL": "host=h", "LEDGER_PATH": "/r/t.ledger.jsonl"}


def test_a_trial_runs_end_to_end_against_a_fake_claude(tmp_path: Path, fake_claude: Path) -> None:
    question = QS.by_id()["eng-01"]
    environ = {**os.environ, "CLAUDECODE": "1", "CLAUDE_CODE_ENTRYPOINT": "cli"}

    ran = run(options(tmp_path, fake_claude, k=2), [question], environ)

    assert ran == 2
    trials_dir = tmp_path / "run" / "trials"
    trial = Trial.from_json(json.loads((trials_dir / "eng-01__t1.json").read_text()))
    assert trial.run_error() is None
    assert [c.short for c in trial.stream.calls] == ["count_by"]
    assert trial.stream.answer == "## Observed Facts"
    assert trial.stream.cost_usd == 0.004
    assert [r["evidence_id"] for r in trial.ledger()] == ["E1"]
    # one ledger per trial
    assert trial.ledger_path != str((trials_dir / "eng-01__t0.ledger.jsonl").resolve())
    seen = json.loads((tmp_path / "fake.json").read_text())
    assert seen["leaked"] == []
    assert seen["stdin"].splitlines()[0].endswith('"content": "/mcp__serenedb-log__engineer bgl"}}')
    assert not Path(seen["cwd"]).exists()  # a fresh temporary directory, removed afterwards
    assert json.loads((tmp_path / "run" / "run.json").read_text())["k"] == 2


def test_finished_trials_are_kept_unless_forced(tmp_path: Path, fake_claude: Path) -> None:
    question = QS.by_id()["eng-01"]
    run(options(tmp_path, fake_claude), [question], os.environ)

    assert run(options(tmp_path, fake_claude), [question], os.environ) == 0
    assert run(options(tmp_path, fake_claude, force=True), [question], os.environ) == 1


def test_a_missing_claude_is_a_driver_error(tmp_path: Path) -> None:
    with pytest.raises(DriverError, match="not on PATH"):
        run(options(tmp_path, tmp_path / "no-such-claude"), QS.select(["eng-01"]), os.environ)


def test_dry_run_prints_the_commands_and_calls_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--model", "claude-haiku-5-5", "--k", "1", "--ids", "mgr-01", "--dry-run"]
    code = main([*argv, "--out", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "--allowedTools 'mcp__serenedb-log__*'" in out or '--allowedTools "mcp__serenedb-log__*"' in out
    assert "/mcp__serenedb-log__manager bgl" in out
    assert not (tmp_path / "trials").exists()
    assert not (tmp_path / "run.json").exists()


def test_main_rejects_an_alias(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--model", "haiku", "--out", str(tmp_path)]) == 2
    assert "pinned" in capsys.readouterr().err
