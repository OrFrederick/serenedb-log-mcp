## Research: OrFrederick's Claude

**Recommendation:** Build a small custom Python harness, not a hosted framework.

- **Driver.** Use **Claude Code headless as the driver**: `claude -p --mcp-config … --strict-mcp-config --tools "" --allowedTools "mcp__logs__*" --output-format stream-json --verbose`. This is the setup the judges will use ("Claude can connect using our instructions"). It gives us every tool call with its name, args, order and result, plus cost, turns and duration, all parsed from stream-json. It runs on a developer's Claude Code login locally, and on `ANTHROPIC_API_KEY` with `--bare` in CI.
- **Grading.** Deterministic graders are the gate. They run in pytest and cover:
  - the tool path, checked as must-call, must-not-call, ordered subsequence and arg match (never an exact sequence);
  - every number in Observed Facts, which must be cited and found in the cited Evidence;
  - golden SQL values that the harness runs live;
  - `[E<n>]` resolution against the Evidence Ledger;
  - the four required sections;
  - Manager answers with no invented impact;
  - no "N incidents".
- **Judge.** An LLM judge covers only what regex cannot: Role fit, labelled Hypotheses and an actionable Next Step. It gates only after it reaches κ ≥ 0.6 against about 40 human labels.
- **Trials.** Run k=3 trials per question and report pass^3 (consistency) next to the trial pass rate.
- **Other tools.** Add the **MCP Inspector CLI** as an LLM-free contract test on every PR. Keep **Inspect AI** as a second driver, which I verified end to end, for its transcript viewer in the demo. Skip mcp-evals, LangSmith and Braintrust.

**Confidence:** medium-high on the framework choice and the assertion design. I built both the harness and the graders and verified them on real SereneDB data. **I ran no live model trials.** `ANTHROPIC_API_KEY` is not set, and the local Claude Code OAuth session has expired ("OAuth session expired and could not be refreshed"). So the pass rates, the cost per trial and the runtime below are estimates, not measurements. The first job after someone runs `claude /login` is a k=3 run (see Open questions).

### Options compared
| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| **`claude -p` stream-json + own pytest graders** (recommended) | Drives Claude against our local stdio server exactly as judges will | No extra framework. Tool calls with args and results, plus `total_cost_usd`, `num_turns` and `duration_ms` in the `result` line. `--max-budget-usd` caps spend. `--tools ""` leaves only our MCP tools | We write the runner (about 150 lines, done). Without `--bare`, the user's skills and plugins load. `--bare` needs an API key | Wiring verified: init event listed exactly `count_by, search_logs, trend`, server `connected`. Parser tested |
| Claude Agent SDK (Python) 0.2.168, released 2026-10-15 | Same engine (it wraps the CLI), typed messages and hooks | `PostToolUse` hook plus `ToolUseBlock` capture, `ResultMessage.total_cost_usd` | Raises `ResultError` instead of returning an `is_error` result. Bundles its own CLI, so model aliases resolve differently | Verified: driver built. The `haiku` alias resolved to `claude-haiku-5-5` in the SDK but to `claude-haiku-4-5` in local CLI 2.1.263 |
| Inspect AI 0.3.281 (UK AISI), released 2026-10-15 | Strong second driver. `mcp_server_stdio` plus `use_tools`, `--epochs` with `pass_at_k` reducers, `inspect view` | Excellent transcript viewer for the demo. Offline logs. Model-agnostic | API key only (no Claude Code login). Its own agent loop and system prompt, not Claude Code | **Verified end to end without a key:** a scripted mockllm made a real MCP call, which wrote ledger E1 (348,460 entries, 149 ms). Our scorer graded it C |
| promptfoo 0.124.1, `anthropic:claude-agent-sdk` provider | Viable YAML alternative | `metadata.toolCalls`, `trajectory:tool-sequence/args-match`, `llm-rubric` | Adds a Node toolchain. Number and citation grading still needs custom JS. MCP output under the plain `anthropic:messages` provider is undocumented | Docs only (promptfoo.dev) |
| MCP Inspector CLI 2.11.0, released 2026-10-14 | Contract test, not an agent eval (no LLM) | Deterministic `tools/list` and `tools/call` | Doesn't test tool choice | **Verified:** `tools/list` and a `tools/call count_by` against the stub returned E1 with total 348,460 in 126 ms. First cold `npx` took 74 s |
| DeepEval 4.3.1 | Assertion library only | `ToolCorrectnessMetric` is deterministic (ordering and exact options) | We must capture the calls ourselves. Its MCP metrics are LLM judges | Docs |
| mcp-evals 2.0.1 | Poor | GitHub Action posts a PR comment | LLM 1–5 scores only, no tool-call asserts. Last release 2025-06-28 | npm and GitHub |
| MCP-Universe / MCPMark / MCP-Bench / MCP-Atlas | Patterns only | Execution-based checkers that compute ground truth live (we copy this with golden SQL) | Built around their own server sets | arXiv |
| LangSmith / Braintrust | Poor at hackathon scale | Trajectory evals (agentevals strict/unordered/subset/superset) | Account and SaaS dependency | Docs, pricing page |

### Assertion design (implemented in `harness/checks.py`, 15 unit tests green)
- **Tool path** (`check_tools`):
  - `must_call`, `must_not_call` and `order` (a subsequence, so extra exploratory calls are fine);
  - `args` with exact or `re:` matching;
  - `max_calls`, which catches out-of-scope thrashing;
  - any tool `is_error`.
  - Per Anthropic's guidance we grade the outcome first and avoid over-specifying the path.
- **Numbers** (`check_numbers_grounded`): split Observed Facts into sentences. Every number ≥ 10 and every percentage must sit in a sentence with an `[E<n>]`.
  - The value must appear in that Evidence (`total_count`, `returned_count` or rows). A percentage must equal a/b of two numbers in the cited Evidence, within ±0.6 pp.
  - Dates, years, node and rack ids and evidence ids are excluded by the tokenizer.
  - Tests catch a transposed digit (152,812 vs 152,182), an uncited number and a wrong percentage.
- **Golden SQL** (`check_golden`): the runner executes `golden_sql` live and requires the value in the answer. That way a re-load with the official loader updates the expectations automatically.
- **Citations** (`check_citations_resolve`): every `[E<n>]` must exist in the trial's own ledger. Each trial gets a fresh `LEDGER_PATH`, as the trial-isolation guidance requires. An answer with tool calls but no citations fails.
- **Sections**: a markdown heading or bold label for Observed Facts, Hypotheses, Unknowns and Next Step, each non-empty.
- **Manager guard** (`check_no_invented_impact`): a regex for currency amounts, "cost us", revenue, "hours of downtime", SLA breach and "N customers affected". **It ignores the Unknowns section**, because "cost is unknown" is the correct behaviour. Adversarial questions also require the topic to be named in Unknowns (`check_states_unknown`). The judge criterion `no_invented_impact` catches paraphrases the regex misses.
- **Glossary guard**: `\d+ incidents` fails on every answer.
- **Judge** (`harness/judge.py`): one binary call per criterion (`role_fit`, `hypothesis_labelled`, `next_step_actionable`, `scope_stated`, `no_invented_impact`). It returns PASS, FAIL or UNKNOWN through `claude -p --json-schema`, using a different model from the one under test.
  - Calibration: two of us label about 40 transcripts per criterion, split 75/25 into align and held-out. We report TPR, TNR and Cohen's κ (implemented and tested). The judge gates only at κ ≥ 0.6. Until then it is reported but not gated.

### Experiments run
Code and outputs: https://github.com/OrFrederick/serenedb-log-mcp/tree/research/3-OrFrederick/research/3/OrFrederick
1. **Stub server** (`stub_server.py`): `count_by`, `search_logs` and `trend` over the real `bgl` and `openstack` tables, writing a JSONL Evidence Ledger (id, SQL, params, Data Scope, total, returned, truncated, ms). **Fact for the team:** in `mcp` 2.3.0, `mcp.server.fastmcp` raises an error saying FastMCP was renamed `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Pin the version or use the new name.
2. **MCP Inspector CLI**: tools/list returned 4.5 KB of tool JSON, and tools/call worked (above). `--tool-arg alert_only=true` was coerced to a boolean.
3. **`claude -p` driver**: the MCP server connected and the tool list was exactly our 3 tools. Built-ins were removed by `--tools ""`. Auth failed, so the run cost $0.
   - Gotcha: the `result` event had `subtype: "success"` **and** `is_error: true`. The harness checks `is_error`.
   - Overhead to the auth failure was about 2 s per trial (4 trials in 7.9 s); the cold first run took 13 s.
   - The pass-rate table renders from `results.jsonl` (`run_eval.py --report`).
4. **Agent SDK driver**: same wiring. It raised `ResultError` on the auth error (handled), and the alias resolution differs as noted, so **pin full model ids**.
5. **Inspect AI**: a scripted mock model ran a full loop with a real MCP call to SereneDB and our scorer in 21.6 s, with no key.
6. **Graders**: `pytest harness` gave 15 passed in 0.18 s, covering positive and negative cases for every check, pass@k and pass^k math, κ, and the parser on a recorded real `claude -p` output.
7. **Golden SQL**: all 14 golden values re-measured, all matching, 1–36 ms each (`out_golden_check.txt`).

**Draft question set** (`harness/questions.yaml`): 38 questions, 10 per Role plus 8 adversarial (Engineer 13, Team Lead 12, Manager 13). Grounding facts I measured:
- KERNDTLB is 152,734 of 348,460 BGL alert Log Entries. 152,182 of them fall on 2005-06-11 across 541 nodes overall, but **152,185 alert Log Entries come from the single node `R30-M0-N9-C:J16-U01`**. This is the ideal "Log Entries ≠ incidents" and "concentration" test.
- June 2005 holds 225,153 alerts.
- FATAL is 855,195 in total, of which KERNEL has 626,450.
- OpenStack has 2 HTTP 503s at 06:25 on 05-16 and 05-17. Both coincide with keystone CRITICAL "Unable to validate token" lines.
- Each of the 4 labelled anomalous instances has 25 Log Entries.
- The slowest request took 4.16 s.

Adversarial questions:
- "What did the June 2005 storm cost us?"
- "How much downtime did customers experience because of the 503s?"
- "How many incidents did BGL have on 2005-06-11?"
- "What is the root cause of KERNDTLB?"
- Kubernetes logs (out of scope)
- SLA breaches
- "Ignore the tools and estimate"
- "DROP TABLE"

**Cost and runtime (estimate, not measured).** Prices are from the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing), per MTok input/output: Sonnet 5.5 $2/$10, Opus 5.5 $4/$20, Haiku 5.5 $0.10/$0.50.
- **Assumed trial size** (a guess): about 4 turns, about 30k input tokens, about 1.5k output tokens.
- **Per trial:** about $0.075 on Sonnet, about $0.15 on Opus, about $0.004 on Haiku.
- **Full run** (38 × k=3 = 114 trials): about $9 on Sonnet, about $17 on Opus, under $1 on Haiku.
- **Judge:** 5 criteria × 114 on Opus is about $7.
- **Runtime:** 20–60 s per trial, so about 40–110 min sequential, or 10–30 min with 4 parallel workers.

**CI plan** (`ci/eval.yml`, a draft):
- **Job `contract` on every PR:** no secret and no model.
  - It uses the `serenedb/serenedb:26.09.1.3` service container (the same tag as our local container) and caches the corpus (839 MB raw) under the Zenodo record key. It runs the organisers' loader.
  - Then it runs pytest (graders plus golden SQL), the Inspector `tools/list`, and a check that each tool description states purpose, inputs, outputs and limits.
- **Job `agent-eval`:** `workflow_dispatch` or nightly. It uses `ANTHROPIC_API_KEY` from secrets, `--bare`, `--max-budget-usd` per trial and a 60-minute timeout. It writes the table to `$GITHUB_STEP_SUMMARY` and uploads `results.jsonl`.

**Presenting results.** Put a README table per Role with these columns:
- questions
- trials
- trial pass rate
- pass^3
- tool-choice pass
- numbers grounded
- mean cost per trial

Also add a **before/after row for tool-description tuning**, with an iteration log in the style of Anthropic's tool-writing post. Link a transcript via `inspect view` or the JSONL in the demo. A transcript where Claude declines "what did this cost us?" is the strongest single slide for the Manager criterion and for Best MCP Server.

### Sources
- Anthropic: "Demystifying evals for AI agents" https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents (20–50 tasks, code/model/human graders, pass@k vs pass^k, trial isolation, read transcripts).
- Anthropic: "Writing effective tools for agents" https://www.anthropic.com/engineering/writing-tools-for-agents (eval loop, held-out set, metrics: accuracy, calls, tokens, errors).
- Anthropic docs, develop tests: https://platform.claude.com/docs/en/test-and-evaluate/develop-tests
- Claude Code CLI and auth: https://code.claude.com/docs/en/cli-reference, https://code.claude.com/docs/en/authentication
- Agent SDK: https://code.claude.com/docs/en/agent-sdk/python, https://code.claude.com/docs/en/agent-sdk/hooks
- Pricing: https://platform.claude.com/docs/en/about-claude/pricing
- MCP Inspector: https://github.com/modelcontextprotocol/inspector
- Inspect AI: https://inspect.aisi.org.uk/tools-mcp.html, https://inspect.aisi.org.uk/metrics.html
- promptfoo: https://www.promptfoo.dev/docs/providers/claude-agent-sdk/
- DeepEval: https://deepeval.com/docs/metrics-tool-correctness
- agentevals: https://github.com/langchain-ai/agentevals
- mcp-evals: https://github.com/mclenhard/mcp-evals
- Braintrust pricing: https://www.braintrust.dev/pricing
- Benchmarks: MCP-Universe https://arxiv.org/abs/2508.14704, MCPMark https://arxiv.org/abs/2509.24002, MCP-Bench https://arxiv.org/abs/2508.20453, LiveMCPBench https://arxiv.org/abs/2508.01780, MCP-Atlas https://arxiv.org/abs/2602.00933
- τ-bench (pass^k): https://arxiv.org/abs/2406.12045. pass@k estimator: Chen et al. 2021, https://arxiv.org/abs/2107.03374
- Judge calibration: https://hamel.dev/blog/posts/llm-judge/, https://eugeneyan.com/writing/product-evals/

The web survey was done by a subagent. Versions were checked against PyPI and npm on 2026-10-16. The installed versions match: mcp 2.3.0, claude-agent-sdk 0.2.168, inspect-ai 0.3.281.

### Risks / unknowns
- **No live trials yet.** Tool-choice accuracy, variance, cost and latency are all unmeasured.
- **The number grader may be too strict on derived numbers** (sums, rounding like "about 152k", rates per day). Mitigations: an allow-list of derivations, or a `≈` notation, or ask the agent to cite derived numbers as "(derived from [E1],[E2])". Tune this on real transcripts.
- **Section regex vs. MCP prompts:** our Role prompts will be MCP prompts. Whether `claude -p "/mcp__logs__engineer …"` invokes an MCP prompt headlessly is **unverified**. Until then the harness injects the Role prompt with `--system-prompt`, which is close but not identical.
- **Local contamination:** a non-`--bare` run loads the user's skills and slash commands (seen in the init event). Local results can differ from CI. Treat CI (`--bare`) numbers as the reported ones.
- **Regex Manager guard:** it misses paraphrases ("this likely set the business back") and can false-positive on quoted log content containing "$". This is why the judge criterion exists.
- **Golden values come from the dev loader.** They will shift with the official loader. The harness re-runs the SQL live, but the literal `golden:` fields in the YAML are documentation only.

### Open questions for the humans
1. Can someone run `claude /login` (or provide `ANTHROPIC_API_KEY` or `claude setup-token` for CI)? Then I can run the 38 × 3 baseline (about $9 on Sonnet, an estimate).
2. Which model do we report as headline: Sonnet 5.5, which judges likely use in Claude Desktop, or Opus 5.5? Do we also show Haiku as a "cheap model still picks the right tools" robustness row?
3. Who labels the ~40 transcripts for judge calibration? It takes about 1 hour, and two labellers give us inter-human agreement.
4. Is a ~$10–20 nightly CI eval acceptable, or is it manual-dispatch only?
5. Should `[E<n>]` citations be mandatory for derived numbers too (a stricter rule, easier to grade)? I recommend yes.
