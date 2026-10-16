# #3 survey findings (NoNaim95's Claude, subagent report saved by parent)

## Recommendation
Build a thin Python harness. The pieces:
- claude-agent-sdk 0.2.168 (MIT) as the driver. Hooks give tool name and args; ResultMessage gives total_cost_usd.
- pytest to run the cases.
- Our own deterministic checkers over the tool trace and the Evidence Ledger.
- A calibrated pass/fail LLM judge, used only for Role fit.

Optional showcase: `claude plugin eval` (Claude Code 2.1.293). Its graders are tool_used, input_match, tool_order, regex and llm. It runs each case 3 times by default and supports --threshold and --max-cost-usd. It has no custom-code graders. If we package the server as a plugin, judges can re-run the suite with one command.

Runner-up: Inspect AI 0.3.281, which has mcp_server_stdio, Epochs, pass_at_k and Wilson CIs. Its react() loop is not the Claude Code harness, though.

Rejected:
- mcp-evals: stale (last push 2025-06), scores 1–5 with an LLM.
- DeepEval and Braintrust: no driver.
- Phoenix: Elastic-2.0 licence.
- promptfoo: viable via its Agent SDK provider, but adds a second stack.

## Assertion layers
- **L0 contract**, no LLM:
  - descriptions are present;
  - closed sets are enums and limit has a maximum;
  - every result has the Evidence fields;
  - truncated == (returned < total).
- **L1 trajectory**: predicates over the set of tool calls (required or forbidden tools, first tool, soft order, argument predicates, budgets), never an exact sequence.
- **L2 grounding**:
  - every [E<n>] resolves in this run's ledger;
  - every number appears in the cited Evidence;
  - golden facts are present;
  - truncated Evidence is not stated as a total;
  - the four sections are present;
  - the Manager blocklist (cost, downtime, customer impact, SLA) applies outside Unknowns.
- **L3 judge**, pass/fail with 3-vote majority. Dimensions: Role fit, hypothesis discipline, pairwise "Roles differ in substance" (run in both orders), adversarial handling. Calibrate on 40–60 human-labelled answers until TPR and TNR are both ≥ 0.85.

## Experiments
All runs used a toy stdio server (spike/) with fake data.

- E1 CLI with defaults (Haiku): the first call was ToolSearch. 7 turns, 15.9 s, $0.0073.
- E1b with ENABLE_TOOL_SEARCH=false: 11 calls, $0.0152.
- E2 lean mode (`--tools "" --setting-sources ""`):

  | Model | Calls | Wall | Cost |
  |---|---|---|---|
  | Haiku | 6 | 10.4 s | $0.0028 |
  | Sonnet | 5 | 15.0 s | $0.0475 |
  | Opus | 6 | 23.0 s | $0.1005 |

- E4 variance: exact call sequences were distinct in 5/5 Haiku runs and 4/5 Sonnet runs. The first tool and the required tool set were stable in 10/10 runs.
- E5 `claude plugin eval`: 2/2 runs passed, 32 s, about $0.004 per run.
- E6 Inspector CLI tools/list works (4 s warm).

Gotchas:
- mcp 2.3.0 renamed FastMCP to `mcp.server.mcpserver.MCPServer`.
- Tool search is on by default.
- Use `tools=[]` and `setting_sources=[]` to isolate the eval.
- `allowed_tools=["mcp__srv__*"]` is required.

## Question set
- 10 questions per Role, 10 adversarial and 5 cross-Role triples: 45 in total.
- Tune on 30 and hold out 15.
- k=5 runs nightly and k=1 per PR.
- Compute Wilson CIs per question. 40/45 passing gives [0.765, 0.952].

## Cost estimate
45 questions × 5 runs:
- Haiku: about $1.1.
- Sonnet: about $19, plus about $9 for the judge.
- Opus: about $38.

## CI
- Every PR: L0 plus golden SQL against a serenedb service container with a fixture slice, plus L1/L2 replayed over committed transcripts. Costs $0.
- Label `run-evals`: 12 questions × 1 run, about $1.
- Nightly: the full suite.

## Sources
- anthropic.com/engineering/writing-tools-for-agents
- anthropic.com/engineering/demystifying-evals-for-ai-agents
- arxiv 2411.00640 (Miller)
- code.claude.com/docs/en/agent-sdk/{mcp,hooks,cost-tracking}
- code.claude.com/docs/en/plugin-evals.md
- inspect.aisi.org.uk/tools-mcp.html
- promptfoo.dev/docs/providers/claude-agent-sdk
- deepeval.com/docs/metrics-tool-correctness
- arxiv 2508.20453, 2508.14704, 2508.01780, 2406.12045
- arxiv 2306.05685
- hamel.dev/blog/posts/llm-judge
- eugeneyan.com/writing/llm-evaluators
- py.sdk.modelcontextprotocol.io/v2/migration
