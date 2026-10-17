#!/usr/bin/env python3
"""Write the `export_investigation` report of a ledger and an answers file to disk (T13).

    uv run python scripts/export_report.py <ledger.jsonl> <answers.yaml> --role <role> [--out <file>]

`--role` is the audience of the report (manager: no raw Log Entry rows in any table). The answers file
holds the title, the answers and optionally evidence_ids (see `serenedb_log_mcp.export.answers`).
Without `--out` the report goes to `$SLM_EXPORT_DIR/<title>-<role>.md` (default `exports/`).

The Markdown is the tool's own: the script calls `export_investigation` in-process with LEDGER_PATH set
to the given ledger, so the file is byte for byte what the tool returns. The tool is read-only; the
script is what writes. No database is needed.

Exit 0 when the report is written, 2 when the ledger, the answers file or the arguments are unusable.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp import config, ledger
from serenedb_log_mcp.config import Role
from serenedb_log_mcp.export import AnswersFileError, load_investigation
from serenedb_log_mcp.ledger import Ledger, LedgerError
from serenedb_log_mcp.server import create_server


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("ledger", type=Path, help="the Evidence Ledger (JSONL, as LEDGER_PATH writes it)")
    parser.add_argument("answers", type=Path, help="the answers file (YAML: title, answers, evidence_ids)")
    parser.add_argument(
        "--role",
        required=True,
        choices=[role.value for role in Role],
        help="the audience of the report; the worked investigation uses engineer",
    )
    parser.add_argument("--out", type=Path, help="where to write the report (default: SLM_EXPORT_DIR)")
    return parser.parse_args(argv)


def slug(title: str) -> str:
    """A file name part for `title`: lower case, words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "investigation"


def export(ledger_path: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call export_investigation on a server reading `ledger_path`; return its structured result."""
    before = config.get_settings()
    config.set_settings(before.model_copy(update={"ledger_path": ledger_path, "role": None}))
    ledger.reset_ledger()
    try:
        server = create_server()
        result = anyio.run(lambda: server.call_tool("export_investigation", arguments))
    finally:
        config.set_settings(before)
        ledger.reset_ledger()
    structured = getattr(result, "structured_content", None)
    if getattr(result, "is_error", False) or not isinstance(structured, dict):
        content = getattr(result, "content", None) or []
        raise ToolError(content[0].text if content else "export_investigation returned no report")
    return structured


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        if not args.ledger.is_file():
            raise LedgerError(f"{args.ledger} does not exist or is not a file")
        Ledger(args.ledger)  # fails now, with the line, if the file is not an Evidence Ledger
        investigation = load_investigation(args.answers)
        report = export(
            args.ledger,
            {
                "role": args.role,
                "title": investigation.title,
                "answers": [answer.model_dump(mode="json") for answer in investigation.answers],
                "evidence_ids": investigation.evidence_ids,
            },
        )
    except (LedgerError, AnswersFileError, ToolError) as exc:
        print(f"export_report: {exc}", file=sys.stderr)
        return 2
    out = args.out or config.get_settings().export_dir / f"{slug(investigation.title)}-{args.role}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report["markdown"], encoding="utf-8")
    shown = ", ".join(report["evidence_ids"]) or "none"
    print(f"Wrote {out}: {report['size_chars']:,} characters, Evidence {shown}.")
    for note in report["notes"]:
        print(f"Note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
