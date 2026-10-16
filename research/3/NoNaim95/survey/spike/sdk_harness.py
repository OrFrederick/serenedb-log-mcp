"""Minimal Claude Agent SDK harness: run one eval question against a local stdio MCP server,
capture tool calls via PreToolUse/PostToolUse hooks and the message stream, return trace + cost."""
import asyncio, json, sys, time
from claude_agent_sdk import (query, ClaudeAgentOptions, HookMatcher, AssistantMessage, ResultMessage, ToolUseBlock)

SERVER = {"toylogs": {"command": "/Users/deni/hackathon/tools/.venv/bin/python",
                      "args": [sys.argv[2] if len(sys.argv) > 2 else "toy_server.py"]}}

async def run(question: str, model: str = "haiku"):
    trace = []
    async def pre(inp, tool_use_id, ctx):
        trace.append({"id": tool_use_id, "tool": inp["tool_name"], "args": inp["tool_input"], "t": time.time()}); return {}
    async def post(inp, tool_use_id, ctx):
        for t in trace:
            if t["id"] == tool_use_id: t["response_chars"] = len(json.dumps(inp.get("tool_response"), default=str))
        return {}
    opts = ClaudeAgentOptions(
        model=model, mcp_servers=SERVER, allowed_tools=["mcp__toylogs__*"],
        tools=[],                       # no built-in tools (Bash/Read/...) -> only our MCP tools
        setting_sources=[],             # ignore user/project settings for reproducibility
        env={"ENABLE_TOOL_SEARCH": "false"},  # load MCP tool schemas upfront, like Claude Desktop with few tools
        max_turns=15, max_budget_usd=0.5,
        hooks={"PreToolUse": [HookMatcher(matcher="^mcp__", hooks=[pre])],
               "PostToolUse": [HookMatcher(matcher="^mcp__", hooks=[post])]},
    )
    final = None; stream_calls = []
    async for m in query(prompt=question, options=opts):
        if isinstance(m, AssistantMessage):
            stream_calls += [b.name for b in m.content if isinstance(b, ToolUseBlock)]
        if isinstance(m, ResultMessage): final = m
    return {"answer": final.result, "cost_usd": final.total_cost_usd, "turns": final.num_turns,
            "duration_ms": final.duration_ms, "usage": final.usage, "hook_trace": trace, "stream_calls": stream_calls}

if __name__ == "__main__":
    out = asyncio.run(run(sys.argv[1]))
    print(json.dumps({k: v for k, v in out.items() if k != "answer"}, indent=1, default=str)[:3000])
    print("ANSWER:", out["answer"][:500])
