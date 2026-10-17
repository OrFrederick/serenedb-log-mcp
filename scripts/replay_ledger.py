#!/usr/bin/env python3
"""Re-run an Evidence Ledger against SereneDB and check that its numbers still hold (T13).

    uv run python scripts/replay_ledger.py <ledger.jsonl> [--answers <answers.yaml>]

Every record is re-run with the statements and parameters it stored (`serenedb_log_mcp.export.replay`
explains how), and compared with what it recorded: totals, Data Scope and summary for every record,
rows for every result that is not sorted by relevance (BM25 ties come in no fixed order). With
`--answers`, every number in the answers is checked against the ledger with `verify/checks.py`, the
checks verify_answer runs. The database is read through DATABASE_URL (and DATABASE_URL_<ROLE>) and
SLM_SCHEMA, as the server reads it; LEDGER_PATH is ignored and nothing is written.

Exit 0 when every record and every answer holds, 1 on any mismatch, 2 when the ledger, the answers
file or the database cannot be used.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from psycopg.conninfo import conninfo_to_dict

from serenedb_log_mcp import db
from serenedb_log_mcp.config import get_settings
from serenedb_log_mcp.export import AnswersFileError, Investigation, load_investigation
from serenedb_log_mcp.export.replay import RecordReplay, ReplayReport, replay
from serenedb_log_mcp.ledger import Ledger, LedgerError

_SHOWN_DSN_KEYS = ("host", "port", "dbname", "user")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("ledger", type=Path, help="the Evidence Ledger (JSONL, as LEDGER_PATH writes it)")
    parser.add_argument("--answers", type=Path, help="the answers file whose numbers to check (YAML)")
    return parser.parse_args(argv)


def database_problem() -> str | None:
    """Why SereneDB cannot be used for the replay, or None when it answers SELECT 1."""
    try:
        db.get_database().fetch_all("SELECT 1")
    except db.DatabaseLayerError as exc:
        return str(exc)
    return None


def where() -> str:
    settings = get_settings()
    try:
        parts = conninfo_to_dict(settings.database_url)
    except Exception:  # the settings already validated it; this is only for the message
        parts = {}
    dsn = " ".join(f"{key}={parts[key]}" for key in _SHOWN_DSN_KEYS if key in parts)
    return f"{dsn or 'DATABASE_URL'} (SLM_SCHEMA {settings.slm_schema})"


def _record_line(item: RecordReplay, stored_statements: int) -> str:
    total = f"{item.total_matches:,}" if isinstance(item.total_matches, int) else str(item.total_matches)
    if any(m.kind in ("call_failed", "not_replayable") for m in item.mismatches):
        return f"{item.evidence_id} {item.tool}: MISMATCH, recorded total_matches {total}"
    status = "ok" if not item.mismatches else "MISMATCH"
    rows = (
        "rows compared"
        if item.rows_compared
        else "rows not compared (sorted by relevance: BM25 ties are unordered)"
    )
    return (
        f"{item.evidence_id} {item.tool}: {status}, {item.statements} of {stored_statements} statements "
        f"re-run as stored, total_matches {total}, {rows}"
    )


def print_report(report: ReplayReport, statements: dict[str, int], answers_path: Path | None) -> None:
    for item in report.records:
        print(_record_line(item, statements.get(item.evidence_id, 0)))
        for mismatch in item.mismatches:
            print(f"  {mismatch.kind}: {mismatch.detail}")
    if answers_path is not None:
        print(f"Answers in {answers_path}, checked with verify/checks.py:")
        for answer in report.answers:
            status = "ok" if not answer.mismatches else f"{len(answer.mismatches)} problem(s)"
            cited = ", ".join(answer.cited) or "no Evidence"
            checked = f"{answer.numbers} numbers checked against {cited}"
            print(f"  {answer.number}. {answer.role}: {status}, {checked}")
            for mismatch in answer.mismatches:
                print(f"     {mismatch.kind}: {mismatch.detail}")
    if report.ok:
        print(f"Replay passed: {len(report.records)} records and {len(report.answers)} answers hold.")
    else:
        print(f"Replay failed: {len(report.mismatches)} mismatch(es).")


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    investigation: Investigation | None = None
    try:
        if not args.ledger.is_file():
            raise LedgerError(f"{args.ledger} does not exist or is not a file")
        records = Ledger(args.ledger).all()
        if not records:
            raise LedgerError(f"{args.ledger} holds no Evidence records, so there is nothing to replay")
        if args.answers is not None:
            investigation = load_investigation(args.answers)
    except (LedgerError, AnswersFileError) as exc:
        print(f"replay_ledger: {exc}", file=sys.stderr)
        return 2
    if (problem := database_problem()) is not None:
        print(f"replay_ledger: cannot replay without SereneDB: {problem}", file=sys.stderr)
        return 2
    print(f"Replaying {len(records)} Evidence records from {args.ledger} against {where()}.")
    report = replay(records, investigation)
    statements = {str(r.get("evidence_id")): len(r.get("sql") or []) for r in records}
    print_report(report, statements, args.answers)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
