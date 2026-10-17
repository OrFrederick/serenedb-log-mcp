#!/usr/bin/env python3
"""Make every call of the demo's beats against SereneDB, time it and record the Evidence Ledger (T24).

    uv run python scripts/demo_record.py [--beats docs/demo/beats.yaml] --ledger <ledger.jsonl> [--replace]

It is the dry run of the tool side of the demo: every call of `docs/demo/beats.yaml` runs in order on an
in-process server, exactly as Claude would make it, and writes a fresh Evidence Ledger that
`scripts/demo_replay.py` reads back offline. Running it right before the demo also warms SereneDB's
caches. It prints per call the Evidence id, the time in SereneDB (query_ms), the wall time and
total_matches, and per beat the tool time against the beat's slot in the run sheet. The time Claude
spends reasoning is not in it: the live run sheet in `docs/demo/script.md` is timed separately.

The database is read through DATABASE_URL (and DATABASE_URL_<ROLE>) and SLM_SCHEMA, as the server reads
them. SLM_QUERY_TIMEOUT_S defaults to 60 s here, not 15 s: under load, whole-BGL searches have needed
more than 15 s (#36). LEDGER_PATH is ignored; `--ledger` names the ledger.

Exit 0 when every call succeeded, 1 when a call failed (the others still run), 2 when the beats file,
the ledger path or the database cannot be used.
"""

from __future__ import annotations

import argparse
import functools
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from serenedb_log_mcp import config, db, ledger
from serenedb_log_mcp.demo import BeatsFileError, DemoBeats, load_beats, resolve_arguments
from serenedb_log_mcp.server import create_server

DEFAULT_BEATS = Path(__file__).resolve().parent.parent / "docs" / "demo" / "beats.yaml"
DEMO_QUERY_TIMEOUT_S = 60.0
"""The demo's SLM_QUERY_TIMEOUT_S when the environment sets none (the server's default is 15 s)."""


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--beats", type=Path, default=DEFAULT_BEATS, help="the beats file (YAML)")
    parser.add_argument("--ledger", type=Path, required=True, help="where to write the Evidence Ledger")
    parser.add_argument(
        "--replace", action="store_true", help="overwrite an existing ledger (otherwise refuse)"
    )
    return parser.parse_args(argv)


@dataclass
class CallTiming:
    beat: str
    tool: str
    role: str
    evidence_id: str | None
    wall_s: float
    query_ms: float | None
    total_matches: Any
    error: str | None = None


def database_problem() -> str | None:
    """Why SereneDB cannot be used, or None when it answers SELECT 1."""
    try:
        db.get_database().fetch_all("SELECT 1")
    except db.DatabaseLayerError as exc:
        return str(exc)
    return None


def demo_settings(ledger_path: Path) -> config.Settings:
    """The process settings with the demo's ledger and, unless the environment sets one, its timeout."""
    settings = config.get_settings()
    update: dict[str, Any] = {"ledger_path": ledger_path, "role": None}
    if not os.environ.get("SLM_QUERY_TIMEOUT_S"):
        update["query_timeout_s"] = DEMO_QUERY_TIMEOUT_S
    return settings.model_copy(update=update)


def record(beats: DemoBeats, server_factory: Callable[[], MCPServer] = create_server) -> list[CallTiming]:
    """Make every call of every beat on one in-process server; return the timings in call order."""
    server = server_factory()
    timings: list[CallTiming] = []
    for beat in beats.beats:
        previous: dict[str, Any] | None = None
        for call in beat.calls:
            role = beat.role_of(call)
            started = time.perf_counter()
            try:
                arguments = {"role": role.value, **resolve_arguments(call, previous)}
                result = anyio.run(functools.partial(server.call_tool, call.tool, arguments))
                structured = getattr(result, "structured_content", None)
                if getattr(result, "is_error", False) or not isinstance(structured, dict):
                    content = getattr(result, "content", None) or []
                    raise ToolError(content[0].text if content else f"{call.tool} returned no result")
            except (ToolError, ValueError) as exc:
                timings.append(
                    CallTiming(
                        beat.title,
                        call.tool,
                        role.value,
                        None,
                        time.perf_counter() - started,
                        None,
                        None,
                        str(exc),
                    )
                )
                previous = None
                continue
            previous = structured
            timings.append(
                CallTiming(
                    beat.title,
                    call.tool,
                    role.value,
                    structured.get("evidence_id"),
                    time.perf_counter() - started,
                    structured.get("query_ms"),
                    structured.get("total_matches"),
                )
            )
    return timings


def _total(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else str(value)


def print_report(beats: DemoBeats, timings: list[CallTiming]) -> None:
    for beat in beats.beats:
        mine = [t for t in timings if t.beat == beat.title]
        if not mine:
            print(f"{beat.title}: no tool call recorded (slot {beat.seconds} s)")
            continue
        wall = sum(t.wall_s for t in mine)
        print(f"{beat.title}: {len(mine)} call(s), {wall:.2f} s of tool time (slot {beat.seconds} s)")
        for t in mine:
            if t.error is not None:
                print(f"  FAILED {t.tool} as {t.role}: {t.error}")
                continue
            query = f"{t.query_ms} ms in SereneDB" if t.query_ms is not None else "no query_ms"
            print(
                f"  {t.evidence_id} {t.tool} as {t.role}: {query}, {t.wall_s:.2f} s wall, "
                f"total_matches {_total(t.total_matches)}"
            )
    wall = sum(t.wall_s for t in timings)
    failed = sum(1 for t in timings if t.error is not None)
    print(
        f"Recorded {len(timings) - failed} of {len(timings)} calls in {wall:.2f} s of tool time; "
        f"the run sheet plans {beats.planned_s} s of {beats.budget_s} s."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        beats = load_beats(args.beats)
    except BeatsFileError as exc:
        print(f"demo_record: {exc}", file=sys.stderr)
        return 2
    if args.ledger.exists() and args.ledger.stat().st_size and not args.replace:
        print(
            f"demo_record: {args.ledger} already holds Evidence; pass --replace to record a fresh ledger",
            file=sys.stderr,
        )
        return 2
    before = config.get_settings()
    config.set_settings(demo_settings(args.ledger))
    ledger.reset_ledger()
    try:
        if (problem := database_problem()) is not None:
            print(f"demo_record: cannot record without SereneDB: {problem}", file=sys.stderr)
            return 2
        args.ledger.parent.mkdir(parents=True, exist_ok=True)
        args.ledger.write_text("", encoding="utf-8")
        settings = config.get_settings()
        print(
            f"Recording {sum(len(b.calls) for b in beats.beats)} calls of {args.beats} into {args.ledger} "
            f"(SLM_SCHEMA {settings.slm_schema}, SLM_QUERY_TIMEOUT_S {settings.query_timeout_s:g})."
        )
        timings = record(beats)
    finally:
        config.set_settings(before)
        ledger.reset_ledger()
    print_report(beats, timings)
    return 1 if any(t.error is not None for t in timings) else 0


if __name__ == "__main__":
    sys.exit(main())
