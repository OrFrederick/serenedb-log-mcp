#!/usr/bin/env python3
"""Run the worked investigation "Start-up path failures" through Claude Code and write the report (T18).

    uv run python scripts/run_worked_investigation.py --model claude-opus-5-5     # calls a real model
    uv run python scripts/run_worked_investigation.py --dry-run                    # prints the commands
    uv run python scripts/run_worked_investigation.py --export-only                # re-renders the report

or `make worked-investigation` (`mk/deliverable.mk`). DATABASE_URL, SLM_SCHEMA and SLM_RAW_SCHEMA from
the environment reach the MCP server, as in eval/driver.py.

The three Role questions of #5 run one after the other, each in its own `claude -p` session against our
MCP server, and every session gets the same `LEDGER_PATH`. So the Evidence ids continue across the
Roles (the Team Lead's first record follows the Engineer's last), and a later Role can cite and
`get_evidence` what an earlier Role produced: the message of each later Role names the ids the earlier
answers cite. Each session starts the way a judge would, with the Role prompt as a slash command
(`/mcp__serenedb-log__<role>`), then the question; the client setup is eval/driver.py's (no tools but
ours, no user settings, `CLAUDE_CODE_*` stripped, a pinned model id).

Every answer is checked with `verify/checks.py`, the checks verify_answer runs, against the shared
ledger; the questions also ask Claude to call verify_answer itself. Then the answers file is written
and `scripts/export_report.py` renders the report with `export_investigation` (`role='engineer'`):

    docs/worked-investigation/ledger.jsonl        the shared Evidence Ledger
    docs/worked-investigation/answers.yaml        title and the three answers, as export_report reads it
    docs/worked-investigation/run.json            model, cost, turns, tool calls and checks per Role
    docs/worked-investigation/<n>-<role>.stream.jsonl   the raw stream-json of each session
    docs/worked-investigation.md                  the report

`--export-only` skips the model and renders the report again from the committed ledger and answers,
so the report can be reproduced without a model; `scripts/replay_ledger.py` re-runs the ledger against
SereneDB. Exit 0 when every answer passes the checks and the report is written, 1 when a session failed
or an answer has problems (the files are still written, for inspection), 2 on a usage error.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import export_report
from serenedb_log_mcp.evidence import Role
from serenedb_log_mcp.export import load_investigation
from serenedb_log_mcp.ledger import Ledger
from serenedb_log_mcp.verify import check_answer

ROOT = Path(__file__).resolve().parent.parent
SERVER = "serenedb-log"
TITLE = "Start-up path failures"
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_OUT = ROOT / "docs" / "worked-investigation"
DEFAULT_REPORT = ROOT / "docs" / "worked-investigation.md"
DEFAULT_BUDGET_USD = 8.0
DEFAULT_MAX_TURNS = 60
DEFAULT_TIMEOUT_S = 1800.0
MODEL_RE = re.compile(r"^claude-[a-z]+-[0-9]+(?:-[0-9]+)*(?:-[0-9]{8})?$")
SERVER_ENV = ("DATABASE_URL", "SLM_SCHEMA", "SLM_RAW_SCHEMA", "SLM_QUERY_TIMEOUT_S", "SLM_PATTERN_TIER1")
_EVIDENCE_ID = re.compile(r"\bE[1-9][0-9]*\b")

SLOW_SPAWNS = (
    "a445709b-6ad0-40ec-8860-bec60b6ca0c2",
    "544fd51c-4edc-4780-baae-ba1d80a0acfc",
    "ae651dff-c7ad-43d6-ac96-bbcd820ccca8",
    "1643649d-2f42-4303-bfcd-7798baec19f9",
)
"""The four instances the OpenStack benchmark labels as anomalous (Loghub's label file). They are
named in the Team Lead's question, the way a user brings a ticket; no tool reads the labels."""

FINAL_MESSAGE = (
    "Call verify_answer with your draft and fix every problem it reports until ok is true. Your final "
    "message must be exactly the verified answer, starting with its Observed Facts heading, with no text "
    "before or after it. Write counts of 1,000 or more with thousands separators, like 12,345."
)


@dataclass(frozen=True)
class Question:
    """One Role's turn in the worked investigation."""

    role: Role
    question: str
    prompt_args: tuple[str, ...] = ()
    """Positional arguments of the Role prompt (dataset, then focus or period)."""

    def slash_command(self) -> str:
        return " ".join([f"/mcp__{SERVER}__{self.role.value}", *self.prompt_args])


QUESTIONS: tuple[Question, ...] = (
    Question(
        Role.ENGINEER,
        "On 2005-09-02 the BGL I/O nodes reported Lustre mount failures for the filesystem /p/gb1. "
        "What failed, on which nodes and in which Component, and what happened on the machine just "
        "before and just after the burst that started at 12:39 (ts as logged)? Start with a full-text "
        "search for the failure, then look at the context around one of the failures, and say what the "
        "next check should be.",
        ("bgl", "KERNMNTF"),
    ),
    Question(
        Role.TEAM_LEAD,
        "Is the /p/gb1 Lustre mount failure a recurring problem? Over the wave from 2005-08-31 to "
        "2005-09-18 (UTC), give its Log Entries next to its Incidents and their Spread: how many "
        "Incidents, on how many days, and how many of them reached every I/O node; show the daily trend "
        "and when the wave ended, and whether KERNMNTF Log Entries go on after it. Then the start-up "
        "path on the second Dataset: the OpenStack benchmark labels four instances as anomalous ("
        + ", ".join(SLOW_SPAWNS)
        + "). Does nova's spawn timing find them, and does a full-text search for errors on those "
        "instances find anything? Finish with an aggregation over the whole Base Corpus, Log Entries "
        "by Team, with its Data Scope, and say which Teams to involve first. Say whether the BGL and "
        "OpenStack start-up problems are linked, and whether such a link would be causal or only "
        "thematic.",
        ("both",),
    ),
    Question(
        Role.MANAGER,
        "Which observed problems deserve attention first? Answer for two Data Scopes: the /p/gb1 wave's "
        "window (2005-08-31 to 2005-09-18, UTC), and the whole BGL Dataset over alert categories, where "
        "the top 5 by priority are a band, not an order. The KERNDTLB storm has by far the most Log "
        "Entries of any alert category: does that make it the top priority? State it as two sentences "
        "exactly in this form, with nothing added inside them and the numbers from list_incidents: "
        '"The largest KERNDTLB Incident has <N> Log '
        'Entries on <N> node (<date>). The category has <N> Log Entries." And how much downtime and '
        "money did these failures cost us?",
        ("bgl",),
    ),
)


@dataclass
class Session:
    """What one Role's `claude -p` session produced."""

    role: str
    question: str
    answer: str = ""
    exit_code: int | None = None
    error: str = ""
    tool_calls: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None
    wall_s: float = 0.0
    evidence: list[str] = field(default_factory=list)
    """The Evidence ids this session added to the ledger."""
    check: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Options:
    model: str = DEFAULT_MODEL
    out: Path = DEFAULT_OUT
    report: Path = DEFAULT_REPORT
    budget_usd: float = DEFAULT_BUDGET_USD
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_s: float = DEFAULT_TIMEOUT_S
    claude_bin: str = "claude"
    server_command: tuple[str, ...] = ()
    dry_run: bool = False


class RunError(RuntimeError):
    """The run cannot start as configured."""


# The client --------------------------------------------------------------------------------------------


def pinned_model(model: str) -> str:
    if not MODEL_RE.fullmatch(model):
        raise RunError(f"--model {model!r} is not a pinned model id such as claude-opus-5-5")
    return model


def clean_env(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment for `claude`: the parent's, without CLAUDE_CODE_* and CLAUDECODE."""
    return {k: v for k, v in environ.items() if not k.startswith("CLAUDE_CODE_") and k != "CLAUDECODE"}


def mcp_config(ledger: Path, command: Sequence[str], environ: Mapping[str, str]) -> dict[str, Any]:
    env = {name: environ[name] for name in SERVER_ENV if name in environ}
    env["LEDGER_PATH"] = str(ledger)
    server = {"type": "stdio", "command": command[0], "args": list(command[1:]), "env": env}
    return {"mcpServers": {SERVER: server}}


def claude_command(options: Options, config: Path) -> list[str]:
    return [
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


def handover(earlier: Sequence[Session]) -> str:
    """What a later Role is told about the earlier answers: their Roles and the Evidence they cite."""
    if not earlier:
        return ""
    parts = []
    for session in earlier:
        cited = list(dict.fromkeys(_EVIDENCE_ID.findall(session.answer)))
        ids = ", ".join(cited) if cited else "none"
        parts.append(f"the {session.role} answer cites {ids}")
    return (
        "This question continues a worked investigation in the same Evidence Ledger: "
        + "; ".join(parts)
        + ". Fetch any of it with get_evidence and cite it as [E<n>] where it supports your answer, "
        "instead of running the same query again; run new queries for what it does not cover."
    )


USER_MESSAGES = ("role prompt", "question")
"""The user messages of a session, each answered by one result event."""


def user_messages(question: Question, earlier: Sequence[Session]) -> list[str]:
    """The stream-json user messages of one session: the Role prompt, then the question."""
    text = "\n\n".join(t for t in (question.question, handover(earlier), FINAL_MESSAGE) if t)
    return [
        json.dumps({"type": "user", "message": {"role": "user", "content": content}})
        for content in (question.slash_command(), text)
    ]


def parse_stream(stdout: str) -> tuple[str, list[str], float | None, int | None, str]:
    """The final answer, our tool calls in order, cost, turns and the run error of a stream-json run."""
    calls: list[str] = []
    last: Mapping[str, Any] | None = None
    results = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", ""))
                    calls.append(name.removeprefix(f"mcp__{SERVER}__"))
        elif event.get("type") == "result":
            last, results = event, results + 1
    if last is None:
        return "", calls, None, None, "no result event"
    if results < len(USER_MESSAGES):
        # Only the Role prompt was answered: the session ended before it answered the question.
        return "", calls, None, None, "the session ended before it answered the question"
    answer = last.get("result") if isinstance(last.get("result"), str) else ""
    error = str(answer or last.get("subtype")) if last.get("is_error") else ""
    cost = last.get("total_cost_usd")
    turns = last.get("num_turns")
    return (
        answer_only(str(answer)),
        calls,
        float(cost) if isinstance(cost, int | float) else None,
        int(turns) if isinstance(turns, int) else None,
        error,
    )


def answer_only(text: str) -> str:
    """The answer from its first Observed Facts heading on: any preface Claude wrote is dropped."""
    match = re.search(r"^#{1,6}\s*Observed Facts\b", text, re.M | re.I)
    return (text[match.start() :] if match else text).strip() + "\n"


def run_session(
    options: Options, index: int, question: Question, earlier: Sequence[Session], environ: Mapping[str, str]
) -> Session:
    ledger = (options.out / "ledger.jsonl").resolve()
    stem = f"{index + 1}-{question.role.value}"
    config = options.out / f"{stem}.mcp.json"
    command = options.server_command or (sys.executable, "-m", "serenedb_log_mcp", "--transport", "stdio")
    config.write_text(json.dumps(mcp_config(ledger, command, environ), indent=1) + "\n", encoding="utf-8")
    before = {r["evidence_id"] for r in Ledger(ledger).all()} if ledger.exists() else set()
    session = Session(role=question.role.value, question=question.question)
    started = time.monotonic()
    stdout = ""
    try:
        # An empty working directory, so no CLAUDE.md or project settings reach the session.
        with tempfile.TemporaryDirectory(prefix="slm-wi-") as cwd:
            proc = subprocess.run(
                claude_command(options, config),
                input="\n".join(user_messages(question, earlier)) + "\n",
                capture_output=True,
                text=True,
                env=clean_env(environ),
                cwd=cwd,
                timeout=options.timeout_s,
                check=False,
            )
        session.exit_code, stdout = proc.returncode, proc.stdout
        if proc.returncode != 0 and not stdout:
            session.error = proc.stderr[-1000:]
    except subprocess.TimeoutExpired as exc:
        session.error = f"the session ran longer than {options.timeout_s:g} s and was stopped"
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
    finally:
        config.unlink(missing_ok=True)  # it names local paths; the stream and run.json keep the setup
    session.wall_s = round(time.monotonic() - started, 1)
    (options.out / f"{stem}.stream.jsonl").write_text(stdout, encoding="utf-8")
    answer, calls, cost, turns, error = parse_stream(stdout)
    session.answer, session.tool_calls, session.cost_usd, session.num_turns = answer, calls, cost, turns
    session.error = session.error or error
    if ledger.exists():
        session.evidence = [r["evidence_id"] for r in Ledger(ledger).all() if r["evidence_id"] not in before]
    return session


# Checks and the report ---------------------------------------------------------------------------------


def check(role: str, answer: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """verify_answer's verdict on `answer` against the shared ledger, as a small JSON-able dict."""
    report = check_answer(Role(role), answer, records, incident_tools=True)
    kinds = sorted({p.kind for p in report.problems})
    return {
        "ok": report.ok,
        "summary": "ok" if report.ok else f"{len(report.problems)} problem(s): {', '.join(kinds)}",
        "problems": [f"{p.kind}: {p.text}" for p in report.problems],
        "cited_evidence": report.cited_evidence,
    }


def write_answers(path: Path, sessions: Sequence[Session]) -> None:
    data = {
        "title": TITLE,
        "answers": [{"role": s.role, "question": s.question, "answer": s.answer} for s in sessions],
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")


def export(options: Options) -> int:
    """Render the report from the ledger and answers file in `options.out` with export_report.py."""
    ledger, answers = options.out / "ledger.jsonl", options.out / "answers.yaml"
    for path in (ledger, answers):
        if not path.is_file():
            print(
                f"run_worked_investigation: {path} does not exist; run without --export-only", file=sys.stderr
            )
            return 2
    records = Ledger(ledger).all()
    failed = [
        f"{a.role.value}: {verdict['summary']}"
        for a in load_investigation(answers).answers
        if not (verdict := check(a.role.value, a.answer, records))["ok"]
    ]
    code = export_report.main(
        [str(ledger), str(answers), "--role", Role.ENGINEER.value, "--out", str(options.report)]
    )
    for line in failed:
        print(f"verify_answer problems: {line}", file=sys.stderr)
    return code or (1 if failed else 0)


def run(options: Options, environ: Mapping[str, str]) -> int:
    if options.dry_run:
        earlier: list[Session] = []
        for index, question in enumerate(QUESTIONS):
            config = options.out / f"{index + 1}-{question.role.value}.mcp.json"
            print(" ".join(_quote(a) for a in claude_command(options, config)))
            for message in user_messages(question, earlier):
                print(f"  stdin: {message}")
            earlier.append(Session(role=question.role.value, question=question.question, answer="[E1]"))
        return 0
    if shutil.which(options.claude_bin) is None:
        raise RunError(f"{options.claude_bin!r} is not on PATH; install Claude Code or pass --claude-bin")
    options.out.mkdir(parents=True, exist_ok=True)
    (options.out / "ledger.jsonl").unlink(missing_ok=True)
    sessions: list[Session] = []
    for index, question in enumerate(QUESTIONS):
        session = run_session(options, index, question, sessions, environ)
        sessions.append(session)
        status = f"RUN ERROR: {session.error[:200]}" if session.error else "ok"
        cost = "-" if session.cost_usd is None else f"${session.cost_usd:.2f}"
        print(
            f"{session.role}: {status}; {len(session.tool_calls)} calls; Evidence "
            f"{', '.join(session.evidence) or 'none'}; {cost}; {session.wall_s} s",
            flush=True,
        )
    records = Ledger(options.out / "ledger.jsonl").all()
    for session in sessions:
        session.check = check(session.role, session.answer, records)
        print(f"{session.role}: {session.check['summary']}")
    meta = {"title": TITLE, "model": options.model, "sessions": [vars(s) for s in sessions]}
    (options.out / "run.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    write_answers(options.out / "answers.yaml", sessions)
    code = export(options)
    if any(s.error for s in sessions):
        return 1
    return code


def _quote(arg: str) -> str:
    return arg if arg and re.fullmatch(r"[\w@%+=:,./-]+", arg) else json.dumps(arg)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"pinned model id (default {DEFAULT_MODEL})")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="directory for ledger, answers, streams"
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="where to write the report")
    parser.add_argument(
        "--budget-usd", type=float, default=DEFAULT_BUDGET_USD, help="--max-budget-usd per Role"
    )
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="--max-turns per Role")
    parser.add_argument(
        "--timeout-s", type=float, default=DEFAULT_TIMEOUT_S, help="wall-clock limit per Role"
    )
    parser.add_argument("--claude-bin", default="claude", help="the Claude Code executable")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print the commands and messages; call no model")
    mode.add_argument(
        "--export-only", action="store_true", help="render the report from the existing ledger and answers"
    )
    args = parser.parse_args(argv)
    try:
        options = Options(
            model=pinned_model(args.model),
            out=args.out,
            report=args.report,
            budget_usd=args.budget_usd,
            max_turns=args.max_turns,
            timeout_s=args.timeout_s,
            claude_bin=args.claude_bin,
            dry_run=args.dry_run,
        )
        if args.export_only:
            return export(options)
        return run(options, os.environ)
    except (RunError, OSError) as exc:
        print(f"run_worked_investigation: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
