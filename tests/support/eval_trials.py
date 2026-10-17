"""Synthetic `claude -p` stream-json runs and trials for the eval tests (T17), with no model call.

The event shapes follow a recorded Claude Code 2.1.29x run (#3 research): a `system`/`init` event
listing the MCP servers and tools, `assistant` events with `tool_use` blocks, `user` events with
`tool_result` blocks, and one `result` event per user message.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from transcript import SERVER, TOOL_PREFIX, Trial, parse_stream

TOOLS = (
    "count_by",
    "describe_corpus",
    "get_context",
    "get_evidence",
    "get_role_brief",
    "latency_stats",
    "search_logs",
    "trend",
    "verify_answer",
)


def init_event(status: str = "connected", tools: Sequence[str] = TOOLS) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "model": "claude-haiku-5-5",
        "mcp_servers": [{"name": SERVER, "status": status}],
        "tools": [TOOL_PREFIX + t for t in tools],
    }


def tool_use(call_id: str, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    block = {"type": "tool_use", "id": call_id, "name": TOOL_PREFIX + tool, "input": dict(arguments)}
    return {"type": "assistant", "message": {"role": "assistant", "content": [block]}}


def tool_result(call_id: str, text: str, *, is_error: bool = False) -> dict[str, Any]:
    block = {
        "type": "tool_result",
        "tool_use_id": call_id,
        "is_error": is_error,
        "content": [{"type": "text", "text": text}],
    }
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def result_event(answer: str, *, cost: float = 0.01, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": answer,
        "total_cost_usd": cost,
        "num_turns": 3,
        "duration_ms": 4200,
    }


def stream_lines(events: Sequence[Mapping[str, Any]]) -> list[str]:
    return [json.dumps(e) for e in events]


def run_events(
    calls: Sequence[tuple[str, Mapping[str, Any]]],
    answer: str,
    *,
    errors: Mapping[int, str] | None = None,
    status: str = "connected",
) -> list[dict[str, Any]]:
    """A whole run: init, the Role prompt's first reply, then each call with its result, then the answer."""
    events: list[dict[str, Any]] = [init_event(status), result_event("What do you want to know?", cost=0.002)]
    for i, (tool, arguments) in enumerate(calls):
        call_id = f"toolu_{i}"
        events.append(tool_use(call_id, tool, arguments))
        error = (errors or {}).get(i)
        events.append(tool_result(call_id, error or '{"evidence_id": "E1"}', is_error=error is not None))
    events.append(result_event(answer))
    return events


def make_trial(
    question_id: str,
    role: str,
    calls: Sequence[tuple[str, Mapping[str, Any]]],
    answer: str,
    *,
    ledger_path: Path | str = "/nonexistent/ledger.jsonl",
    repeat: int = 0,
    errors: Mapping[int, str] | None = None,
    status: str = "connected",
) -> Trial:
    stream = parse_stream(stream_lines(run_events(calls, answer, errors=errors, status=status)))
    return Trial(
        question_id=question_id,
        role=role,
        model="claude-haiku-5-5",
        repeat=repeat,
        stream=stream,
        ledger_path=str(ledger_path),
        wall_s=5.0,
        exit_code=0,
    )
