## Research: ugnmura's Codex

**Recommendation:** Use pytest + official MCP Python SDK for protocol and database-golden checks, and promptfoo's native Claude Agent SDK provider for repeated live Claude trajectory/answer eval. Join captured calls to each run's real Evidence Ledger, verify numeric claims against independent executed golden SQL, and apply a human-calibrated rubric only to qualitative Role fit/action/causal restraint. Inspector is an interoperability probe. Keep exact full tool trajectories flexible; enforce necessary capabilities, argument predicates and dependency order. No live Claude accuracy has been measured yet.

**Confidence:** high in the contract/assertion design and official SDK compatibility, medium in the live framework selection until its pinned provider passes a real Claude smoke test.

### Options compared

| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| **pytest + official MCP SDK + promptfoo Claude Agent SDK** | Recommended split | Deterministic contracts plus actual agent calls, captured tool metadata, repeat/report support | Node added; actual adapter/model run pending | [Promptfoo provider](https://www.promptfoo.dev/docs/providers/claude-agent-sdk/), [our report](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/3-ugnmura/research/3/ugnmura/report.md) |
| Direct Claude Agent SDK runner | Strong Python fallback | MCP config, stream/hook capture, budgets and answer schema | Own reporting/repetition plumbing | [SDK](https://platform.claude.com/docs/en/agent-sdk/python) |
| Inspect AI | Strong Python alternative | Native stdio/HTTP MCP, custom scorers/repeats | Its agent loop differs from intended host | [MCP guide](https://inspect.aisi.org.uk/tools-mcp.html) |
| MCP Inspector | Necessary manual/CI transport probe | Direct discovery, prompts/get, tools/call | Does not test autonomous Claude choice | [Inspector](https://modelcontextprotocol.io/docs/tools/inspector) |
| mcp-evals / MCP-Bench-style | Borrow benchmark design | MCP tasks, model grading/state checks | Generic scores do not certify our SQL or Roles | [mcp-evals](https://github.com/mclenhard/mcp-evals), [MCP-Bench](https://github.com/Accenture/mcp-bench) |
| DeepEval | Optional scorer library | Tool/arguments/output/order comparisons | Still needs agent runner and domain goldens | [Tool correctness](https://deepeval.com/docs/metrics-tool-correctness) |
| LangSmith / Braintrust | Optional hosted comparison | Traces, datasets and custom/human scoring | More service/account plumbing than core needs | [AgentEvals](https://github.com/langchain-ai/agentevals), [Braintrust Claude integration](https://www.braintrust.dev/docs/integrations/sdk-integrations/claude-agent-sdk) |

### Experiments run

Official **mcp==2.3.0**: actual in-process discovery, three prompts, prompts/get, structured tool calls, missing-argument/limit rejection all passed in `auto`, `legacy`, `2026-07-28`. Actual subprocess/stdio tests also passed in legacy and modern eras. Seven intentionally invalid typed answers were rejected (wrong numeric value, wrong metric, wrong Data Scope, dangling citations, missing section and invented financial loss); a positive “cost/downtime unknown” answer passed. A deliberately false additional free-text number **still passes** the typed checker: measure/render or independently extract all prose claims before claiming all-number verification.

These use real OpenStack counts from an offline scan, explicitly marked fixture/non-SQL. They establish harness mechanics, **not** Claude accuracy or SereneDB correctness. No model calls or database queries were made. [Code, results and report](https://github.com/OrFrederick/serenedb-log-mcp/tree/research/3-ugnmura/research/3/ugnmura).

Draft [question bank](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/3-ugnmura/research/3/ugnmura/questions.json): 10 per Role + 12 adversarial. Proposed trusted PR smoke 18 trials; full nightly 42×5=210 trials, with cache disabled, fresh sessions, all-gates pass rate and all-five consistency by Role. Five clean trials per question remain statistically weak; report denominators and case-cluster uncertainty. PR/fork deterministic tests require no model secrets; authenticated live CI is a separate trusted job. Measure one pilot before estimating spend/runtime; use turn/time/budget caps. README must say **live quality not run** until it is.

### Sources

[Full comparison and assertion/CI design](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/3-ugnmura/research/3/ugnmura/report.md); primary links above. [Anthropic eval guidance](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) supports mixed graders/repeated trials; our exact cadence is a proposal.

### Risks / unknowns

Final loader/schema/Ownership Table and SQL goldens are pending. SDK answer JSON enforcement does not establish native Desktop/Code Markdown behavior. MCP outputSchema constrains tool results, not the final answer. MCP `allowed_tools` is auto-approval, not visibility/authentication. Live runs must retrieve and use the actual Role prompt; MCP connection alone does not prove prompt selection. No keys were present and no Claude CLI was invoked.

### Open questions for the humans

Which exact Claude model and CI budget should be used? Which host versions should be supported? Confirm whether final worked-investigation tables may render from validated typed claims; otherwise semantic extraction plus human review is required to validate all natural-language numeric claims.
