"""One eval trial as recorded: the `claude -p --output-format stream-json` events, parsed (T17).

`parse_stream(lines)` reads the stream-json events of one `claude -p` run and returns what the graders
need: the init event (which MCP servers connected and which tools Claude was offered), every tool call
with its arguments in call order, every tool result with its error flag, the final answer and the
run's cost, turns and duration. It never raises on a malformed line; such lines are counted.

The driver sends two user messages per trial (the Role prompt as a slash command, then the question),
so a run has one `result` event per message. The answer is the last result's text; cost, turns and
duration come from the last result too, because Claude Code reports them cumulatively per session.

A `result` event can carry `subtype: "success"` and `is_error: true` together (#3 research, an
authentication failure), so the run error is read from `is_error`, not from the subtype.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SERVER = "serenedb-log"
TOOL_PREFIX = f"mcp__{SERVER}__"
MAX_RESULT_TEXT = 2000
"""Tool result texts are cut to this many characters in the trial record."""


def short_name(name: str) -> str:
    """`mcp__serenedb-log__count_by` -> `count_by`; any other name is returned unchanged."""
    return name[len(TOOL_PREFIX) :] if name.startswith(TOOL_PREFIX) else name


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]

    @property
    def short(self) -> str:
        return short_name(self.name)

    @property
    def ours(self) -> bool:
        return self.name.startswith(TOOL_PREFIX)


@dataclass(frozen=True)
class ToolResult:
    tool_use_id: str
    is_error: bool
    text: str


@dataclass(frozen=True)
class Stream:
    """What one stream-json run contained."""

    model: str | None = None
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    calls: list[ToolCall] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    answer: str = ""
    result_events: int = 0
    is_error: bool = False
    error_text: str = ""
    cost_usd: float | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    bad_lines: int = 0

    def server_status(self) -> str | None:
        """The status of our MCP server in the init event ('connected', 'failed', ...), if listed."""
        for server in self.mcp_servers:
            if server.get("name") == SERVER:
                status = server.get("status")
                return str(status) if status is not None else None
        return None


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(json.dumps(block, default=str))
        return "\n".join(parts)
    return "" if content is None else json.dumps(content, default=str)


def parse_stream(lines: Iterable[str]) -> Stream:
    """Parse the stream-json lines of one `claude -p` run."""
    model: str | None = None
    servers: list[dict[str, Any]] = []
    tools: list[str] = []
    calls: list[ToolCall] = []
    results: list[ToolResult] = []
    last: Mapping[str, Any] | None = None
    result_events = 0
    bad = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not isinstance(event, Mapping):
            bad += 1
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            model = event.get("model") if isinstance(event.get("model"), str) else model
            servers = [dict(s) for s in event.get("mcp_servers") or [] if isinstance(s, Mapping)]
            tools = [t for t in event.get("tools") or [] if isinstance(t, str)]
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                    arguments = block.get("input")
                    calls.append(
                        ToolCall(
                            id=str(block.get("id", "")),
                            name=str(block.get("name", "")),
                            input=dict(arguments) if isinstance(arguments, Mapping) else {},
                        )
                    )
        elif kind == "user":
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, Mapping) and block.get("type") == "tool_result":
                    results.append(
                        ToolResult(
                            tool_use_id=str(block.get("tool_use_id", "")),
                            is_error=bool(block.get("is_error")),
                            text=_text_of(block.get("content"))[:MAX_RESULT_TEXT],
                        )
                    )
        elif kind == "result":
            result_events += 1
            last = event
    if last is None:
        return Stream(model, [dict(s) for s in servers], tools, calls, results, bad_lines=bad)
    answer = last.get("result")
    is_error = bool(last.get("is_error"))
    errors = last.get("errors")
    error_text = ""
    if is_error:
        error_text = str(answer or errors or last.get("subtype") or "the run reported is_error")
    cost = last.get("total_cost_usd")
    turns = last.get("num_turns")
    duration = last.get("duration_ms")
    return Stream(
        model=model,
        mcp_servers=[dict(s) for s in servers],
        tools=tools,
        calls=calls,
        results=results,
        answer=answer if isinstance(answer, str) else "",
        result_events=result_events,
        is_error=is_error,
        error_text=error_text[:MAX_RESULT_TEXT],
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
        num_turns=int(turns) if isinstance(turns, int) else None,
        duration_ms=int(duration) if isinstance(duration, int | float) else None,
        bad_lines=bad,
    )


@dataclass(frozen=True)
class Trial:
    """One trial: one question, one model, one repeat, with its own Evidence Ledger."""

    question_id: str
    role: str
    model: str
    repeat: int
    stream: Stream
    ledger_path: str
    wall_s: float
    exit_code: int | None
    stderr_tail: str = ""
    driver_error: str = ""
    """Set when the driver could not run `claude` at all (not found, timeout)."""

    def run_error(self) -> str | None:
        """Why the run itself failed (infrastructure, not the model), or None."""
        if self.driver_error:
            return self.driver_error
        if self.stream.result_events == 0:
            return f"no result event (exit code {self.exit_code}); stderr: {self.stderr_tail[-300:]}"
        status = self.stream.server_status()
        if status != "connected":
            return f"MCP server {SERVER!r} status {status!r} in the init event"
        if self.stream.is_error:
            return f"the run reported is_error: {self.stream.error_text[:300]}"
        return None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Trial:
        raw = dict(data["stream"])
        raw["calls"] = [ToolCall(**c) for c in raw.get("calls", [])]
        raw["results"] = [ToolResult(**r) for r in raw.get("results", [])]
        fields = {k: v for k, v in data.items() if k != "stream"}
        return cls(stream=Stream(**raw), **fields)

    def ledger(self) -> list[dict[str, Any]]:
        """The trial's Evidence Ledger records; an absent file is an empty ledger."""
        return read_ledger(Path(self.ledger_path))


def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Read an Evidence Ledger JSONL file, skipping an unterminated last line (ledger.py semantics)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    records: list[dict[str, Any]] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records
