"""Inspect AI variant of the harness: same graders, Inspect drives the agent loop + MCP stdio server.

  Live:   inspect eval harness/inspect_task.py --model anthropic/claude-sonnet-5-5 --epochs 3 --epochs-reducer pass_at_1
  No-key wiring check (scripted mock model makes real MCP calls against SereneDB):
          python harness/inspect_task.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml
from inspect_ai import Task, eval as inspect_eval, task
from inspect_ai.dataset import Sample
from inspect_ai.model import ChatMessageAssistant, ModelOutput, get_model
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer
from inspect_ai.solver import TaskState, generate, system_message, use_tools
from inspect_ai.tool import mcp_server_stdio

sys.path.insert(0, str(Path(__file__).parent))
import checks as C  # noqa: E402
from evtrace import ToolCall, Trace, read_ledger  # noqa: E402

HERE = Path(__file__).parent
LEDGER = Path(os.environ.get("LEDGER_PATH", tempfile.mkdtemp() + "/ledger.jsonl"))


@scorer(metrics=[accuracy()])
def deterministic():
    async def score(state: TaskState, target: Target) -> Score:
        q = state.metadata
        calls = [ToolCall(tc.function, tc.arguments) for m in state.messages
                 if isinstance(m, ChatMessageAssistant) for tc in (m.tool_calls or [])]
        tr = Trace(q["id"], final_text=state.output.completion, tool_calls=calls, ledger=read_ledger(LEDGER))
        res = {"tools": C.check_tools(tr, q.get("must_call", ()), q.get("must_not_call", ()), q.get("order", ()),
                                      q.get("args"), q.get("max_calls")),
               "sections": C.check_sections(tr), "citations": C.check_citations_resolve(tr),
               "numbers": C.check_numbers_grounded(tr)}
        ok = all(v[0] for v in res.values())
        return Score(value=CORRECT if ok else INCORRECT, explanation=json.dumps({k: v[1] for k, v in res.items()}))
    return score


@task
def log_mcp_eval(ids: str = ""):
    qs = yaml.safe_load(open(HERE / "questions.yaml"))
    if ids:
        qs = [q for q in qs if q["id"] in ids.split(",")]
    server = mcp_server_stdio(name="logs", command=sys.executable, args=[str(HERE.parent / "stub_server.py")],
                              env={"LEDGER_PATH": str(LEDGER)})
    return Task(dataset=[Sample(input=f"[Role: {q['role']}] {q['question']}", id=q["id"], metadata=q) for q in qs],
                solver=[system_message((HERE / "role_prompt.txt").read_text()), use_tools(server), generate()],
                scorer=deterministic())


if __name__ == "__main__":
    # Scripted "model": call count_by for real, then answer citing E1. Proves MCP wiring + scorer without a key.
    LEDGER.unlink(missing_ok=True)
    outs = [ModelOutput.for_tool_call("mockllm/model", "count_by",
                                      {"dataset": "bgl", "group_by": "alert_label", "alert_only": True, "limit": 3}),
            ModelOutput.from_content("mockllm/model",
                                     "## Observed Facts\n- KERNDTLB: 152,734 alert Log Entries [E1] of 348,460 [E1].\n"
                                     "## Hypotheses\n- none\n## Unknowns\n- cost is unknown\n## Next Step\n- check R30")]
    logs = inspect_eval(log_mcp_eval(ids="tl-01"), model=get_model("mockllm/model", custom_outputs=outs),
                        log_dir=str(HERE.parent / "out_inspect_logs"), display="none")
    s = logs[0].samples[0]
    print("status:", logs[0].status, "| score:", s.scores["deterministic"].value, "|", s.scores["deterministic"].explanation)
    print("ledger:", [(e["evidence_id"], e["total_count"], e["query_ms"]) for e in read_ledger(LEDGER)])
