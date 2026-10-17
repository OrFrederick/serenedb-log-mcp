#!/usr/bin/env python3
"""Replay a recorded Evidence Ledger for the demo, offline and without SereneDB (T24, demo insurance).

    uv run python scripts/demo_replay.py <ledger.jsonl> [--beats docs/demo/beats.yaml] [--beat N]
        [--answers <answers.yaml>] [--draft <draft.md> --role <role>] [--rows N] [--pause]

It reads the ledger (from `scripts/demo_record.py`, or LEDGER_PATH of a live session) and prints what
the agent got, so the demo can go on when SereneDB or the network is not there:

- With `--beats`, beat by beat: the title, the prompt as typed, the Evidence to expect, then every
  recorded tool call of the beat with its arguments, Data Scope, totals, query time and first rows.
  Without it, every record of the ledger in order. A live session's ledger maps by tool and Role
  where its arguments differ from the run sheet, and says so. `--beat N` shows one beat (1-based).
- With `--answers`, every answer of an answers file (the format of `export_investigation`), each
  checked offline with the checks `verify_answer` runs.
- With `--draft` and `--role`, one draft answer checked the same way: the self-verification beat.

Rows are shown as the record's Role received them: none for the Manager when they are raw Log Entries,
at most 3 for the Team Lead. Nothing here opens a database connection or writes a file.

Exit 0 when every answer and draft checked passes (or none was given), 1 when one has problems, 2 when
the ledger, the beats, the answers or the draft cannot be used.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from serenedb_log_mcp.config import Role
from serenedb_log_mcp.demo import BeatMatch, BeatsFileError, load_beats, match_beats
from serenedb_log_mcp.export import AnswersFileError, load_investigation
from serenedb_log_mcp.ledger import Ledger, LedgerError
from serenedb_log_mcp.roles.shaping import RAW_LINE_COLUMN, RAW_LINE_TOOLS, TEAM_LEAD_SAMPLE_ROWS
from serenedb_log_mcp.server import create_server
from serenedb_log_mcp.verify import INCIDENT_TOOLS, AnswerReport, check_answer

CELL_CHARS = 60
"""Longest cell shown; longer values (messages, SQL) are cut with an ellipsis."""


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("ledger", type=Path, help="the recorded Evidence Ledger (JSONL)")
    parser.add_argument("--beats", type=Path, help="group the records by the beats of this file (YAML)")
    parser.add_argument("--beat", type=int, help="show only this beat (1-based; needs --beats)")
    parser.add_argument("--answers", type=Path, help="check and show the answers of this file (YAML)")
    parser.add_argument("--draft", type=Path, help="check this draft answer (Markdown; needs --role)")
    parser.add_argument("--role", choices=[role.value for role in Role], help="the Role of --draft")
    parser.add_argument("--rows", type=int, default=5, help="rows shown per record (default 5)")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before each beat")
    args = parser.parse_args(argv)
    if args.beat is not None and args.beats is None:
        parser.error("--beat needs --beats")
    if (args.draft is None) != (args.role is None):
        parser.error("--draft and --role go together")
    return args


def offers_incident_tools() -> bool:
    """Whether this server offers list_incidents or top_patterns, so answers are checked as
    verify_answer checks them here. Building the server registers tools and connects to nothing."""
    get_tool = getattr(getattr(create_server(), "_tool_manager", None), "get_tool", None)
    if get_tool is None:
        return True
    return any(get_tool(name) is not None for name in INCIDENT_TOOLS)


# Records -------------------------------------------------------------------------------------------


def _cell(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= CELL_CHARS else text[: CELL_CHARS - 1] + "…"


def _number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def _raw_line(record: Mapping[str, Any]) -> bool:
    return record.get("tool") in RAW_LINE_TOOLS or RAW_LINE_COLUMN in (record.get("columns") or [])


def _scope(scope: Mapping[str, Any]) -> str:
    parts = [", ".join(scope.get("datasets") or []) or "no Dataset"]
    if scope.get("time_from") or scope.get("time_to"):
        parts.append(f"{scope.get('time_from') or 'start'} to {scope.get('time_to') or 'end'}")
    filters = {k: v for k, v in (scope.get("filters") or {}).items() if v is not None}
    if filters:
        parts.append(", ".join(f"{k}={_cell(v)}" for k, v in filters.items()))
    return "; ".join(parts)


def _shown_rows(record: Mapping[str, Any], limit: int) -> tuple[list[Any], str | None]:
    rows = list(record.get("rows") or [])
    if _raw_line(record) and record.get("role") == Role.MANAGER.value and rows:
        return [], "rows withheld: they are raw Log Entries, and the Manager receives none"
    if _raw_line(record) and record.get("role") == Role.TEAM_LEAD.value:
        limit = min(limit, TEAM_LEAD_SAMPLE_ROWS)
    if len(rows) > limit:
        return rows[:limit], f"first {limit} of {len(rows)} returned rows"
    return rows, None


def record_lines(record: Mapping[str, Any], rows: int) -> list[str]:
    """One recorded tool call as terminal text: call, Data Scope, totals, summary and first rows."""
    arguments = {k: v for k, v in (record.get("arguments") or {}).items() if v is not None}
    call = ", ".join(f"{k}={_cell(v)}" for k, v in arguments.items())
    lines = [
        f"[{record.get('evidence_id')}] {record.get('tool')}(role={record.get('role')}, {call})",
        f"  Data Scope: {_scope(record.get('data_scope') or {})}",
        f"  total_matches {_number(record.get('total_matches'))}, "
        f"returned {_number(record.get('returned'))}, "
        f"truncated: {'yes' if record.get('truncated') else 'no'}, "
        f"{record.get('query_ms')} ms in SereneDB",
    ]
    summary = {
        k: v
        for k, v in (record.get("summary") or {}).items()
        if v is not None and not isinstance(v, (dict, list))
    }
    if summary:
        lines.append("  summary: " + ", ".join(f"{k}={_cell(v)}" for k, v in summary.items()))
    shown, note = _shown_rows(record, rows)
    columns = [str(c) for c in record.get("columns") or []]
    if shown:
        lines.append("  | " + " | ".join(columns) + " |")
        for row in shown:
            cells = row if isinstance(row, list) else [row]
            lines.append("  | " + " | ".join(_cell(c) for c in cells) + " |")
    elif not record.get("rows"):
        lines.append("  no rows")
    if note:
        lines.append(f"  ({note})")
    return lines


# Beats, answers and drafts ---------------------------------------------------------------------------


def beat_lines(number: int, match: BeatMatch, rows: int) -> list[str]:
    beat = match.beat
    who = f", {beat.role.value}" if beat.role else ""
    lines = [
        f"== Beat {number}: {beat.title} ({beat.seconds} s{who})",
        f"Prompt: {beat.prompt}",
        f"Expect: {beat.expected}",
    ]
    for record in match.records:
        lines += record_lines(record, rows)
        if record.get("evidence_id") in match.loose:
            lines.append("  (made with other arguments than the run sheet's call)")
    for call in match.missing:
        lines.append(f"  not in the ledger: {call.tool} {_cell(call.arguments)}")
    if not beat.calls:
        lines.append("  (no recorded tool call in this beat)")
    return [*lines, ""]


def verdict_lines(report: AnswerReport) -> list[str]:
    """The verify_answer verdict on one answer, as the tool would summarise it."""
    if report.ok:
        cited = ", ".join(report.cited_evidence) or "no Evidence"
        lines = [f"verify_answer: OK, {len(report.checked_numbers)} numbers checked against {cited}"]
    else:
        lines = [f"verify_answer: {len(report.problems)} problem(s)"]
        for problem in report.problems:
            lines.append(f"  {problem.kind} in {problem.section}: {_cell(problem.text)}")
            lines.append(f"    hint: {problem.hint}")
    return [*lines, *(f"  note: {note}" for note in report.notes)]


def main(argv: Sequence[str] | None = None, wait: Callable[[str], Any] = input) -> int:
    args = _arguments(argv)
    try:
        if not args.ledger.is_file():
            raise LedgerError(f"{args.ledger} does not exist or is not a file")
        records = Ledger(args.ledger).all()
        if not records:
            raise LedgerError(f"{args.ledger} holds no Evidence records, so there is nothing to replay")
        beats = load_beats(args.beats) if args.beats is not None else None
        if beats is not None and args.beat is not None and not 1 <= args.beat <= len(beats.beats):
            raise BeatsFileError(f"--beat {args.beat}: {args.beats} has beats 1 to {len(beats.beats)}")
        investigation = load_investigation(args.answers) if args.answers is not None else None
        draft = args.draft.read_text(encoding="utf-8") if args.draft is not None else None
    except (LedgerError, BeatsFileError, AnswersFileError, OSError) as exc:
        print(f"demo_replay: {exc}", file=sys.stderr)
        return 2

    print(f"Replaying {len(records)} Evidence records from {args.ledger}, offline.")
    print()
    if beats is None:
        for record in records:
            print("\n".join(record_lines(record, args.rows)))
        print()
    else:
        matches, leftover = match_beats(beats, records)
        for number, match in enumerate(matches, start=1):
            if args.beat is not None and number != args.beat:
                continue
            if args.pause:
                wait(f"[Enter] beat {number}: {match.beat.title} ")
            print("\n".join(beat_lines(number, match, args.rows)))
        if leftover and args.beat is None:
            print(f"== Evidence outside the beats ({len(leftover)} records)")
            for record in leftover:
                print("\n".join(record_lines(record, args.rows)))
            print()

    failed = 0
    incident_tools = offers_incident_tools()
    if investigation is not None:
        print(f"== Answers: {investigation.title}")
        for number, answer in enumerate(investigation.answers, start=1):
            report = check_answer(answer.role, answer.answer, records, incident_tools=incident_tools)
            failed += not report.ok
            print(f"{number}. {answer.role.value}: {answer.question}")
            print(answer.answer.rstrip())
            print("\n".join(verdict_lines(report)))
            print()
    if draft is not None:
        report = check_answer(args.role, draft, records, incident_tools=incident_tools)
        failed += not report.ok
        print(f"== Draft {args.draft} for {args.role}")
        print(draft.rstrip())
        print("\n".join(verdict_lines(report)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
