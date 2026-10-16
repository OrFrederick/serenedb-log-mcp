"""Eval driver: run Claude against the stub MCP server, capture tool calls + final answer + cost.

Two back-ends:
  cli : `claude -p ... --output-format stream-json --verbose --mcp-config ... --strict-mcp-config`
  sdk : claude_agent_sdk.query(...) with mcp_servers config

Each run gets its own Evidence Ledger file (LEDGER_PATH passed to the server via env),
so assertions can resolve [E<n>] against exactly the Evidence of that run.

Usage:
  python driver.py --backend cli --ids eng-01,mgr-03 --repeats 2 --model haiku --out runs/
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PY = "/Users/deni/hackathon/tools/.venv/bin/python"
SERVER = "logs"  # tool names become mcp__logs__<tool>

ROLE_PROMPTS = {
    "Engineer": "You are answering for an Engineer investigating one failure: what failed, in which Component, "
                "what happened just before and just after. Show the relevant error lines and nearby events, "
                "a possible cause, and the next check to run.",
    "Team Lead": "You are answering for a Team Lead: which problems recur, how often, and which Components are "
                 "involved over a stated period. Use counts and trends; say what to investigate first.",
    "Manager": "You are answering for a Manager: which observed problems deserve attention first. Give a short "
               "prioritised explanation of risks and actions using ONLY measured frequency and affected Components. "
               "Never state financial loss, downtime, or customer impact: the logs cannot establish them; list them under Unknowns.",
}
COMMON = """You investigate logs ONLY through the `logs` MCP tools (SereneDB). Rules:
- Every number you state must come from a tool result and be followed by its citation, e.g. "12,345 Log Entries [E3]".
- Matching Log Entries are NOT incidents; never call them incidents.
- Structure the final answer with exactly these markdown headings: ## Observed Facts, ## Hypotheses, ## Unknowns, ## Next Step.
- State the Data Scope (Dataset, time range, filters) of every count.
- If a question asks for something the logs cannot establish, say so under Unknowns instead of guessing."""


def system_prompt(role: str) -> str:
    return COMMON + "\n\nRole: " + role + ". " + ROLE_PROMPTS[role]


def mcp_config(ledger: str) -> dict:
    return {"mcpServers": {SERVER: {"type": "stdio", "command": PY,
                                    "args": ["-I", str(HERE / "stub_server.py")],
                                    "env": {"LEDGER_PATH": ledger}}}}


def clean_env() -> dict:
    # Strip parent Claude Code session vars so the child is a fresh, independent session.
    return {k: v for k, v in os.environ.items()
            if not (k.startswith("CLAUDE_CODE_") or k in {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_JOB_DIR"})}


def run_cli(q: dict, ledger: str, model: str | None, max_turns: int, budget: float) -> dict:
    cfg = Path(ledger).with_suffix(".mcp.json")
    cfg.write_text(json.dumps(mcp_config(ledger)))
    cmd = ["claude", "-p", q["question"], "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "",
           "--allowedTools", f"mcp__{SERVER}", "--setting-sources", "",
           "--append-system-prompt", system_prompt(q["role"]), "--no-session-persistence",
           "--max-turns", str(max_turns), "--max-budget-usd", str(budget)]
    if model:
        cmd += ["--model", model]
    t = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, env=clean_env(), cwd=tempfile.gettempdir(), timeout=600)
    wall = time.time() - t
    calls, answer, result, mcp_status, results = [], "", {}, None, []
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            mcp_status = ev.get("mcp_servers")
        if ev.get("type") == "assistant":
            for b in ev["message"].get("content", []):
                if b.get("type") == "tool_use":
                    calls.append({"name": b["name"], "input": b.get("input", {})})
        if ev.get("type") == "user":
            for b in ev.get("message", {}).get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    results.append({"is_error": bool(b.get("is_error")), "head": json.dumps(b.get("content"))[:300]})
        if ev.get("type") == "result":
            result = ev
            answer = ev.get("result") or ""
    return {"calls": calls, "tool_results": results, "answer": answer, "wall_s": round(wall, 1),
            "cost_usd": result.get("total_cost_usd"), "num_turns": result.get("num_turns"),
            "usage": result.get("usage"), "is_error": result.get("is_error", p.returncode != 0),
            "model_usage": result.get("modelUsage"), "mcp_status": mcp_status,
            "stderr": p.stderr[-2000:] if p.returncode else ""}


async def _run_sdk(q: dict, ledger: str, model: str | None, max_turns: int, budget: float) -> dict:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ResultMessage, SystemMessage,
                                  ToolResultBlock, ToolUseBlock, UserMessage, query)
    cfg = mcp_config(ledger)["mcpServers"]
    opts = ClaudeAgentOptions(mcp_servers=cfg, allowed_tools=[f"mcp__{SERVER}"], tools=[],
                              system_prompt={"type": "preset", "preset": "claude_code",
                                             "append": system_prompt(q["role"])},
                              setting_sources=[], max_turns=max_turns, model=model,
                              cwd=tempfile.gettempdir(), strict_mcp_config=True,
                              max_budget_usd=budget, cli_path=os.environ.get("CLAUDE_CLI", shutil.which("claude")))
    calls, answer, res, mcp_status, results = [], "", None, None, []
    t = time.time()
    async for m in query(prompt=q["question"], options=opts):
        if isinstance(m, SystemMessage) and m.subtype == "init":
            mcp_status = m.data.get("mcp_servers")
        if isinstance(m, AssistantMessage):
            for b in m.content:
                if isinstance(b, ToolUseBlock):
                    calls.append({"name": b.name, "input": b.input})
        if isinstance(m, UserMessage) and isinstance(m.content, list):
            for b in m.content:
                if isinstance(b, ToolResultBlock):
                    results.append({"is_error": bool(b.is_error), "head": json.dumps(b.content, default=str)[:300]})
        if isinstance(m, ResultMessage):
            res = m
            answer = m.result or ""
    return {"calls": calls, "tool_results": results, "answer": answer, "wall_s": round(time.time() - t, 1),
            "cost_usd": res.total_cost_usd if res else None, "num_turns": res.num_turns if res else None,
            "usage": res.usage if res else None, "is_error": res.is_error if res else True,
            "model_usage": getattr(res, "model_usage", None), "mcp_status": mcp_status, "stderr": ""}


def run_sdk(q, ledger, model, max_turns, budget):
    # NB: replacing os.environ wholesale with clean_env() made the SDK-spawned CLI report
    # 'Not logged in' (claude.ai OAuth); popping only the session vars works.
    for k in [k for k in os.environ if (k.startswith('CLAUDE_CODE_') and k != 'CLAUDE_CODE_EXECPATH') or k == 'CLAUDECODE']:
        os.environ.pop(k)
    return asyncio.run(_run_sdk(q, ledger, model, max_turns, budget))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["cli", "sdk"], default="cli")
    ap.add_argument("--questions", default=str(HERE / "questions.yaml"))
    ap.add_argument("--ids", default="")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--budget", type=float, default=0.75, help="per-run USD cap (cli only)")
    ap.add_argument("--out", default=str(HERE / "runs"))
    a = ap.parse_args()
    qs = yaml.safe_load(open(a.questions))["questions"]
    if a.ids:
        want = a.ids.split(",")
        qs = [q for q in qs if q["id"] in want]
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    fn = run_cli if a.backend == "cli" else run_sdk
    for q in qs:
        for r in range(a.repeats):
            tag = f"{q['id']}__{a.backend}__{a.model or 'default'}__r{r}"
            ledger = str((out / f"{tag}.ledger.jsonl").resolve())
            Path(ledger).unlink(missing_ok=True)
            rec = fn(q, ledger, a.model, a.max_turns, a.budget)
            rec.update({"id": q["id"], "role": q["role"], "backend": a.backend, "model": a.model,
                        "repeat": r, "ledger": ledger})
            (out / f"{tag}.json").write_text(json.dumps(rec, indent=1, default=str))
            print(f"{tag}: {len(rec['calls'])} calls, ${rec['cost_usd']}, {rec['wall_s']}s, "
                  f"err={rec['is_error']} tools={[c['name'].split('__')[-1] for c in rec['calls']]}", flush=True)


if __name__ == "__main__":
    main()
