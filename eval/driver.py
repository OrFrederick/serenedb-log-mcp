#!/usr/bin/env python3
"""Eval driver (T17): run each question through `claude -p` against our MCP server, k trials each.

Usage (DATABASE_URL and SLM_SCHEMA from the environment reach the server):
    uv run python eval/driver.py --model claude-haiku-5-5 --k 3 --out eval/runs/haiku-k3
    uv run python eval/driver.py --model claude-sonnet-5-5 --k 1 --ids eng-01,mgr-02 --split holdout
    uv run python eval/driver.py --model claude-haiku-5-5 --k 1 --ids eng-01 --dry-run   # no LLM call

or `make eval MODEL=claude-haiku-5-5 K=3`, which also grades the trials and writes the report.

The setup is the client the judges use, with nothing of the developer's own Claude Code leaking in
(#3 research):

    claude -p --input-format stream-json --output-format stream-json --verbose
        --mcp-config <trial>.mcp.json --strict-mcp-config --tools "" --setting-sources ""
        --allowedTools "mcp__serenedb-log__*" --max-budget-usd <B> --max-turns <T>
        --model <pinned id> --no-session-persistence

- The model id is pinned (`claude-haiku-5-5`, not `haiku`): aliases resolve differently in the CLI
  and the Agent SDK (#3).
- Every trial gets a fresh Evidence Ledger (`LEDGER_PATH` in the server's environment) and a fresh
  empty working directory, so its `[E<n>]` citations resolve against its own Evidence only.
- The Role prompt is invoked as the slash command `/mcp__serenedb-log__<role> [args]`, the way a
  judge would start, and the question follows as a second user message in the same session.
  `--role-delivery system` instead appends the rendered prompt as a system prompt, for comparison.
- `CLAUDE_CODE_*` and `CLAUDECODE` are removed from the environment, so a `claude` started from
  inside a Claude Code session runs as a fresh, independent session.

Each trial writes `<out>/trials/<id>__t<n>.json` (the parsed trial, see transcript.Trial), the raw
`.stream.jsonl`, the `.ledger.jsonl` and the `.mcp.json` it ran with. An existing trial file is
kept and not run again unless `--force` is given, so an interrupted run resumes where it stopped.
Grading and the report are eval/report.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from questions import DEFAULT_PATH, Question, QuestionError, load
from serenedb_log_mcp.roles.prompts import render
from transcript import SERVER, Trial, parse_stream

ROOT = Path(__file__).resolve().parent.parent
MODEL_RE = re.compile(r"^claude-[a-z]+-[0-9]+(?:-[0-9]+)*(?:-[0-9]{8})?$")
"""A pinned model id such as claude-haiku-5-5 or claude-sonnet-4-5-20250929; aliases are refused."""
STRIPPED_PREFIX = "CLAUDE_CODE_"
STRIPPED_NAMES = frozenset({"CLAUDECODE"})
SERVER_ENV = ("DATABASE_URL", "SLM_SCHEMA", "SLM_RAW_SCHEMA", "SLM_QUERY_TIMEOUT_S", "SLM_PATTERN_TIER1")
"""Settings passed through to the MCP server explicitly, besides LEDGER_PATH."""

DEFAULT_BUDGET_USD = 0.75
DEFAULT_MAX_TURNS = 20
DEFAULT_TIMEOUT_S = 600.0


class DriverError(RuntimeError):
    """The driver cannot run as configured."""


@dataclass(frozen=True)
class Options:
    model: str
    k: int
    out: Path
    budget_usd: float = DEFAULT_BUDGET_USD
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_s: float = DEFAULT_TIMEOUT_S
    role_delivery: str = "slash"
    claude_bin: str = "claude"
    server_command: tuple[str, ...] = ()
    force: bool = False
    dry_run: bool = False
    bare: bool = False


def pinned_model(model: str) -> str:
    if not MODEL_RE.fullmatch(model):
        raise DriverError(
            f"--model {model!r} is not a pinned model id. Pass a full id such as claude-haiku-5-5 or "
            "claude-sonnet-5-5: aliases like 'haiku' resolve differently in the CLI and the Agent SDK."
        )
    return model


def clean_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment for `claude`: the parent's, without CLAUDE_CODE_* and CLAUDECODE."""
    return {k: v for k, v in environ.items() if not k.startswith(STRIPPED_PREFIX) and k not in STRIPPED_NAMES}


def default_server_command() -> tuple[str, ...]:
    """Start the server with this interpreter, so the trial runs the checkout's code and venv."""
    return (sys.executable, "-m", "serenedb_log_mcp", "--transport", "stdio")


def mcp_config(ledger: Path, command: Sequence[str], environ: Mapping[str, str]) -> dict[str, Any]:
    env = {name: environ[name] for name in SERVER_ENV if name in environ}
    env["LEDGER_PATH"] = str(ledger)
    return {
        "mcpServers": {
            SERVER: {"type": "stdio", "command": command[0], "args": list(command[1:]), "env": env}
        }
    }


def claude_command(options: Options, question: Question, config: Path) -> list[str]:
    cmd = [
        options.claude_bin,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--mcp-config",
        str(config),
        "--strict-mcp-config",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--allowedTools",
        f"mcp__{SERVER}__*",
        "--max-budget-usd",
        f"{options.budget_usd:g}",
        "--max-turns",
        str(options.max_turns),
        "--model",
        options.model,
        "--no-session-persistence",
    ]
    if options.bare:
        cmd.append("--bare")
    if options.role_delivery == "system":
        cmd += ["--append-system-prompt", render(question.role, **question.prompt_args)]
    return cmd


def user_messages(options: Options, question: Question) -> list[str]:
    """The stream-json user messages of one trial: the Role prompt as a slash command, then the question."""
    texts = [question.question]
    if options.role_delivery == "slash":
        texts.insert(0, question.slash_command(SERVER))
    return [json.dumps({"type": "user", "message": {"role": "user", "content": text}}) for text in texts]


def trial_stem(question: Question, repeat: int) -> str:
    return f"{question.id}__t{repeat}"


def run_trial(options: Options, question: Question, repeat: int, environ: Mapping[str, str]) -> Trial:
    trials = options.out / "trials"
    trials.mkdir(parents=True, exist_ok=True)
    stem = trial_stem(question, repeat)
    ledger = (trials / f"{stem}.ledger.jsonl").resolve()
    stream_path = trials / f"{stem}.stream.jsonl"
    config = trials / f"{stem}.mcp.json"
    ledger.unlink(missing_ok=True)
    command = options.server_command or default_server_command()
    config.write_text(json.dumps(mcp_config(ledger, command, environ), indent=1) + "\n", encoding="utf-8")
    cmd = claude_command(options, question, config)
    stdin = "\n".join(user_messages(options, question)) + "\n"

    started = time.monotonic()
    exit_code: int | None = None
    stdout = stderr = driver_error = ""
    with tempfile.TemporaryDirectory(prefix="slm-eval-") as cwd:
        try:
            proc = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                env=clean_env(environ),
                cwd=cwd,
                timeout=options.timeout_s,
                check=False,
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except FileNotFoundError:
            driver_error = f"{options.claude_bin!r} was not found; install Claude Code or pass --claude-bin"
        except subprocess.TimeoutExpired as exc:
            driver_error = f"the trial ran longer than {options.timeout_s:g} s and was stopped"
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
    wall = time.monotonic() - started
    stream_path.write_text(stdout, encoding="utf-8")
    trial = Trial(
        question_id=question.id,
        role=question.role.value,
        model=options.model,
        repeat=repeat,
        stream=parse_stream(stdout.split("\n")),
        ledger_path=str(ledger),
        wall_s=round(wall, 2),
        exit_code=exit_code,
        stderr_tail=stderr[-2000:],
        driver_error=driver_error,
    )
    (trials / f"{stem}.json").write_text(json.dumps(trial.to_json(), indent=1) + "\n", encoding="utf-8")
    return trial


def summary_line(trial: Trial) -> str:
    tools = [c.short for c in trial.stream.calls]
    cost = "-" if trial.stream.cost_usd is None else f"${trial.stream.cost_usd:.4f}"
    error = trial.run_error()
    status = f"RUN ERROR: {error[:120]}" if error else "ok"
    return (
        f"{trial.question_id} t{trial.repeat}: {status}; {len(tools)} calls {tools}; {cost}; {trial.wall_s} s"
    )


def run(options: Options, questions: Sequence[Question], environ: Mapping[str, str]) -> int:
    """Run every trial that has no result yet; returns the number of trials run."""
    if not options.dry_run and shutil.which(options.claude_bin) is None:
        raise DriverError(f"{options.claude_bin!r} is not on PATH; install Claude Code or pass --claude-bin")
    meta = {
        "model": options.model,
        "k": options.k,
        "role_delivery": options.role_delivery,
        "budget_usd": options.budget_usd,
        "max_turns": options.max_turns,
        "questions": [q.id for q in questions],
    }
    if not options.dry_run:
        options.out.mkdir(parents=True, exist_ok=True)
        (options.out / "run.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    ran = 0
    for question in questions:
        for repeat in range(options.k):
            done = options.out / "trials" / f"{trial_stem(question, repeat)}.json"
            if done.exists() and not options.force:
                continue
            if options.dry_run:
                config = options.out / "trials" / f"{trial_stem(question, repeat)}.mcp.json"
                print(" ".join(_quote(a) for a in claude_command(options, question, config)))
                for message in user_messages(options, question):
                    print(f"  stdin: {message}")
                continue
            trial = run_trial(options, question, repeat, environ)
            ran += 1
            print(summary_line(trial), flush=True)
    return ran


def _quote(arg: str) -> str:
    return arg if arg and re.fullmatch(r"[\w@%+=:,./-]+", arg) else json.dumps(arg)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="driver.py", description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("--model", required=True, help="pinned model id, for example claude-haiku-5-5")
    parser.add_argument("--k", type=int, default=3, help="trials per question (default 3)")
    parser.add_argument("--out", type=Path, required=True, help="run directory (trials/ goes inside)")
    parser.add_argument("--questions", type=Path, default=DEFAULT_PATH, help="the question file")
    parser.add_argument("--ids", default="", help="comma-separated question ids (default: all)")
    parser.add_argument("--split", choices=["tune", "holdout"], help="only questions of this split")
    parser.add_argument(
        "--budget-usd", type=float, default=DEFAULT_BUDGET_USD, help="--max-budget-usd per trial"
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="--max-turns per trial")
    parser.add_argument(
        "--timeout-s", type=float, default=DEFAULT_TIMEOUT_S, help="wall-clock limit per trial"
    )
    parser.add_argument(
        "--role-delivery",
        choices=["slash", "system"],
        default="slash",
        help="slash: the Role prompt as a slash command (default); system: as an appended system prompt",
    )
    parser.add_argument("--claude-bin", default="claude", help="the Claude Code executable")
    parser.add_argument(
        "--bare",
        action="store_true",
        help="pass --bare (CI with ANTHROPIC_API_KEY: no hooks or plugins)",
    )
    parser.add_argument("--force", action="store_true", help="run trials again that already have a result")
    parser.add_argument("--dry-run", action="store_true", help="print the commands; call no model")
    args = parser.parse_args(argv)
    try:
        if args.k < 1:
            raise DriverError("--k must be at least 1")
        options = Options(
            model=pinned_model(args.model),
            k=args.k,
            out=args.out,
            budget_usd=args.budget_usd,
            max_turns=args.max_turns,
            timeout_s=args.timeout_s,
            role_delivery=args.role_delivery,
            claude_bin=args.claude_bin,
            force=args.force,
            dry_run=args.dry_run,
            bare=args.bare,
        )
        ids = [i.strip() for i in args.ids.split(",") if i.strip()]
        questions = load(args.questions).select(ids, args.split)
        ran = run(options, questions, os.environ)
    except (DriverError, QuestionError, OSError) as exc:
        print(f"driver: {exc}", file=sys.stderr)
        return 2
    if not args.dry_run:
        print(f"{ran} trial(s) run into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
