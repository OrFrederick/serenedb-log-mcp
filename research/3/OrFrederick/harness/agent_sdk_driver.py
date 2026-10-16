"""Alternative driver: claude-agent-sdk with PreToolUse/PostToolUse hooks capturing MCP calls."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage, SystemMessage,
                              ToolUseBlock, query)

from evtrace import ToolCall, Trace, read_ledger


async def run_agent_sdk(question_id: str, prompt: str, server_cmd: list[str], model="sonnet",
                        system_prompt=None, max_turns=12, max_budget_usd=0.5) -> Trace:
    work = Path(tempfile.mkdtemp(prefix=f"eval-sdk-{question_id}-"))
    ledger = work / "ledger.jsonl"
    tr = Trace(question_id)
    post: dict[str, dict] = {}

    async def on_post(inp, tool_use_id, ctx):  # PostToolUse: result per tool_use_id
        post[tool_use_id] = {"name": inp.get("tool_name"), "response": inp.get("tool_response")}
        return {}

    opts = ClaudeAgentOptions(
        mcp_servers={"logs": {"type": "stdio", "command": server_cmd[0], "args": server_cmd[1:],
                              "env": {"LEDGER_PATH": str(ledger)}}},
        strict_mcp_config=True, tools=[], allowed_tools=["mcp__logs__*"], setting_sources=[],
        model=model, system_prompt=system_prompt, max_turns=max_turns, max_budget_usd=max_budget_usd, cwd=str(work),
        hooks={"PostToolUse": [HookMatcher(matcher="^mcp__", hooks=[on_post])]},
    )
    ids = []
    try:
        await _consume(query(prompt=prompt, options=opts), tr, ids)
    except Exception as e:  # SDK raises ResultError on API/auth errors instead of yielding is_error
        tr.is_error, tr.error = True, f"{type(e).__name__}: {e}"[:300]
    for tc, i in zip(tr.tool_calls, ids):
        if i in post:
            tc.result = json.dumps(post[i]["response"], default=str)[:4000]
    tr.ledger = read_ledger(ledger)
    return tr


async def _consume(stream, tr: Trace, ids: list):
    async for m in stream:
        if isinstance(m, SystemMessage) and m.subtype == "init":
            tr.tools_offered = [t.split("__", 2)[-1] for t in m.data.get("tools", [])]
            tr.model = m.data.get("model", "")
        elif isinstance(m, AssistantMessage):  # ToolUseBlocks are the reliable source of order + args
            for b in m.content:
                if isinstance(b, ToolUseBlock):
                    tr.tool_calls.append(ToolCall(b.name.split("__", 2)[-1], b.input)); ids.append(b.id)
        elif isinstance(m, ResultMessage):
            tr.final_text, tr.cost_usd, tr.num_turns = m.result or "", m.total_cost_usd or 0, m.num_turns
            tr.duration_ms, tr.is_error = m.duration_ms, m.is_error
            tr.error = (m.terminal_reason or "") if m.is_error else ""


if __name__ == "__main__":
    here = Path(__file__).parent
    t = asyncio.run(run_agent_sdk("smoke", sys.argv[1] if len(sys.argv) > 1 else "How many BGL Log Entries are there?",
                                  [sys.executable, str(here.parent / "stub_server.py")], model="haiku", max_turns=4))
    print(json.dumps({"tools_offered": t.tools_offered, "model": t.model, "calls": [c.name for c in t.tool_calls],
                      "is_error": t.is_error, "error": t.error, "text": t.final_text[:200], "cost": t.cost_usd}))
