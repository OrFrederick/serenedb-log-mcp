# #3 harness findings (subagent report, saved by parent). See report_pilot.md, report_full_haiku.md, results_*.json.

## Contents
- stub_server.py: 5 tools with an Evidence ledger.
- driver.py: two backends, `cli` and `sdk`.
- assertions.py: deterministic checks.
- grade.py: produces the reports.
- test_contract.py: 23 tests, all passing, no LLM.
- test_eval.py: gate checks fail the run; style checks only warn.
- questions.yaml: 36 questions (10/10/10 per Role + 6 adversarial), 22 with golden SQL.
- ci/eval.yml: CI workflow.

## Driver
- CLI: `claude -p --output-format stream-json --verbose --mcp-config --strict-mcp-config --tools "" --allowedTools mcp__logs --setting-sources ""`.
- SDK: claude_agent_sdk 0.2.168.
- Strip the `CLAUDE_CODE_*` / `CLAUDECODE` env vars, otherwise the run reports "Not logged in".
- `total_cost_usd` is an estimate at list price.
- Default model when none is set: claude-opus-5-5.

## Results

| Run | Full gate | $ per run | Median wall time |
|---|---|---|---|
| Haiku, 36 questions ×1 | 21/36 | 0.0019 | 15.7 s |
| Haiku, pilot ×3 | 14/18 | 0.0014 | — |
| Haiku SDK, ×1 | 6/6 | — | — |
| Sonnet, ×3 | 17/18 | 0.023 | — |
| Opus, ×2 | 5/12 | 0.0705 | 21.6 s |

Total spend: about $2.35.

### Checks at 100% for every model
- tool selection and arguments
- all four sections present
- citations resolve
- golden numbers present
- Manager guard (no invented cost)
- Unknowns flagged
- no counts called "incidents"

### Main failure
Numeric grounding: Opus 6/12. Opus rounds and brackets numbers ("fewer than 2,500", "~32k–36k").

### Variance
- The full-gate result flipped between repeats on 4/6 questions for Haiku and Opus, and on 1/6 for Sonnet.
- Use ≥3 repeats per question, and 5 for the README.

## Bugs caught
- A ledger write-ordering bug in the server.
- Hidden tool errors: SDK 2.x reports only "Error executing tool X", and the model then wrote a tidy "unavailable" answer. Fixed with a wrapper and an infra.tool_errors check.
- Wrong arithmetic: 855,186 vs the true 854,986.
- Sums computed by the model itself. Tools should return share_pct, top_n_sum and other_count.

## Not done
- LLM judge not prototyped.
- CI load time not measured.
