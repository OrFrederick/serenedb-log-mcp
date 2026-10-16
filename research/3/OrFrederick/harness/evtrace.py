"""Run one question through Claude against a local stdio MCP server and capture a Trace.

Two drivers, same Trace output:
  * run_claude_p  - Claude Code headless (`claude -p --output-format stream-json`). Uses the
    Claude Code login locally or ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN in CI.
  * run_agent_sdk - claude-agent-sdk (Python) with a PostToolUse hook (see agent_sdk_driver.py).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolCall:
    name: str            # bare tool name, "mcp__logs__" prefix stripped
    args: dict
    result: str | None = None
    is_error: bool = False


@dataclass
class Trace:
    question_id: str
    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tools_offered: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    num_turns: int = 0
    duration_ms: int = 0
    model: str = ""
    is_error: bool = False
    error: str = ""
    ledger: list[dict] = field(default_factory=list)   # Evidence Ledger entries written during this run


def _strip(name: str) -> str:
    return name.split("__", 2)[-1] if name.startswith("mcp__") else name


def parse_stream_json(lines, question_id: str = "") -> Trace:
    """Parse `claude -p --output-format stream-json --verbose` output into a Trace."""
    tr = Trace(question_id)
    by_id: dict[str, ToolCall] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            tr.tools_offered = [_strip(x) for x in ev.get("tools", [])]
            tr.model = ev.get("model", "")
        elif t == "assistant":
            for block in ev["message"].get("content", []):
                if block.get("type") == "tool_use":
                    tc = ToolCall(_strip(block["name"]), block.get("input", {}))
                    by_id[block["id"]] = tc
                    tr.tool_calls.append(tc)
        elif t == "user":
            content = ev.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    c = block.get("content")
                    if isinstance(c, list):
                        c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
                    tc = by_id[block["tool_use_id"]]
                    tc.result, tc.is_error = c, bool(block.get("is_error"))
        elif t == "result":
            # NB: subtype can be "success" while is_error is true (seen on auth failure).
            tr.final_text = ev.get("result") or ""
            tr.cost_usd = ev.get("total_cost_usd") or 0.0
            tr.num_turns = ev.get("num_turns") or 0
            tr.duration_ms = ev.get("duration_ms") or 0
            tr.is_error = bool(ev.get("is_error"))
            if tr.is_error:
                tr.error = ev.get("terminal_reason") or tr.final_text
    return tr


def read_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def run_claude_p(question_id: str, prompt: str, *, server_cmd: list[str], system_prompt: str | None = None,
                 model: str = "sonnet", max_turns: int = 12, max_budget_usd: float = 0.5,
                 bare: bool | None = None, server_env: dict | None = None, timeout_s: int = 300) -> Trace:
    """One isolated trial: fresh ledger, fresh cwd, only our MCP tools, no built-in tools."""
    work = Path(tempfile.mkdtemp(prefix=f"eval-{question_id}-"))
    ledger = work / "ledger.jsonl"
    cfg = {"mcpServers": {"logs": {"type": "stdio", "command": server_cmd[0], "args": server_cmd[1:],
                                   "env": {"LEDGER_PATH": str(ledger), **(server_env or {})}}}}
    (work / "mcp.json").write_text(json.dumps(cfg))
    if bare is None:  # --bare needs ANTHROPIC_API_KEY; it skips user CLAUDE.md, hooks, plugins (clean trial)
        bare = bool(os.environ.get("ANTHROPIC_API_KEY"))
    cmd = ["claude", "-p", prompt, "--mcp-config", str(work / "mcp.json"), "--strict-mcp-config",
           "--tools", "", "--allowedTools", "mcp__logs__*", "--output-format", "stream-json", "--verbose",
           "--model", model, "--max-turns", str(max_turns), "--max-budget-usd", str(max_budget_usd),
           "--no-session-persistence"]
    cmd += ["--bare"] if bare else ["--setting-sources", ""]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
    tr = parse_stream_json(p.stdout.splitlines(), question_id)
    tr.duration_ms = tr.duration_ms or int((time.time() - t0) * 1000)
    tr.ledger = read_ledger(ledger)
    if p.returncode and not tr.is_error:
        tr.is_error, tr.error = True, p.stderr[-500:]
    return tr
