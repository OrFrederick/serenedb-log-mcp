# #2 survey findings (subagent report, saved by parent; probe/experiment code in this dir)

## Measured Claude Code 2.1.293 behaviour (probe_server*.py, probe_claude_code_run*.jsonl)

### Result content
- When a result has `structuredContent`, the model sees only that JSON (compact). `content` text blocks are dropped. Related issues: anthropics/claude-code #79944, #45575, #55677, #64316.
- So put everything the model needs in `structuredContent`. Make `content` the same JSON, which follows the spec SHOULD and keeps other clients working.
- On `isError` results, only `content[0].text` reaches the model (#86032).

### Errors in mcp 2.3.0
- A plain exception reaches the model as "Error executing tool X", with no message.
- Use `ToolError` (`mcp.server.mcpserver.exceptions`) for every error the model should read.

### Tool loading
- Tools are deferred behind ToolSearch by default. Use `_meta={"anthropic/alwaysLoad": True}` or `.mcp.json` `"alwaysLoad": true`.
- Server instructions load at startup. The cap is 4,096 chars.
- The model never sees `outputSchema` (#90150). Summarise the output fields in each description.

### Size limits
- Warning at 10k tokens; truncation at 25k (`MAX_MCP_OUTPUT_TOKENS`).
- More than 50,000 chars spills to a file. At 60k chars Claude got only a file path.
- Per-tool override: `_meta["anthropic/maxResultSizeChars"]`.

### Token cost per format
Measured on 50 BGL hits:

| Format | Tokens |
|---|---|
| Indented JSON | 5,104 |
| Compact JSON | 4,053 |
| columns+rows JSON | 3,030 (≈61 per row) |
| Markdown | 3,231 |
| TSV | 2,865 |

## MCP spec and SDK

### Spec 2026-07-28
- Sessions are gone. State lives in server-minted handles passed as arguments, so the Ledger is keyed by evidence_id.
- Annotation defaults are pessimistic, so set `readOnlyHint=True` and `openWorldHint=False` explicitly.
- Return tools in deterministic order.
- Prompts are user-controlled. In Claude Code, arguments split on whitespace.
- Resources are not auto-read.

### mcp 2.3.0 (2026-10-02)
- `FastMCP` was renamed to `MCPServer` (`from mcp.server import MCPServer`).
- `-> str` tools get wrapped as `{"result": ...}`. Return Pydantic envelope models instead.
- `list[...]` returns are split into one block per item.

### Alternatives
- Standalone fastmcp 4.1.0 adds nothing we need.

## SereneDB 26.10.1 safety (exp_safety*.py)

### Timeouts
- `statement_timeout` is accepted but not enforced. A query ran 6.7 s.
- psycopg `cancel_safe()` works: QueryCanceled at 3.46 s from a 1 s timer, and the connection stays usable.
- `pg_cancel_backend` is slow (14 s).

### Read-only
- `default_transaction_read_only` is not enforced.
- `BEGIN READ ONLY` / psycopg `conn.read_only=True` blocks writes. FTS, GROUP BY and time_bucket all work inside it.

### SELECT-only LOGIN role
- INSERT, CREATE and DROP are denied.
- But it can `read_csv('/etc/passwd')`, `glob('/var/lib/serenedb/*')`, and `SET memory_limit` / `enable_external_access` server-wide. Report this to the organisers.

### Bound parameters
- These work, including `message @@ %s` with BM25 + ts_highlight.
- An injection string passed as a value matched 0 rows.

## Reference servers

| Server | Tools | Notes |
|---|---|---|
| Grafana | 119 (64 default) | Loki: limit default 10, cap 100, limit+1 truncation detection, metadata{resultsTruncated}, hints{} on empty results, 24h / 100GiB guardrail with rewrite guidance |
| Datadog | 383 (core 23) | search vs analyze (SQL) split; max_tokens param; CSV ≈ half the tokens of JSON |
| ClickHouse | 3 | raw SQL, readonly=1 |
| Postgres MCP Pro | 9 | read-only tx plus pglast |
| SigNoz | 43 | search vs aggregate split; best-practices doc: truncation in machine-readable metadata with next step, boundary vs nearest neighbour in every description |
| Last9 | 54 | 10–20 default rows, cap 100, did_you_mean, get_service_profile |
| Axiom | — | 7 MCP prompts as investigation workflows |

## Tool count vs accuracy
- Degradation sets in at 30–50 tools:
  - Anthropic advanced-tool-use;
  - RAG-MCP arXiv 2505.03275 (13.62% vs 43.13%);
  - LiveMCPBench 2508.01780.
- Similar-looking tools are the real risk:
  - Mem2ActBench: 94.5 → 69.75;
  - "MCP Tool Descriptions Are Smelly!" 2602.14878: 97.1% of descriptions have a defect; fixing them gives +5.85 points.

## Recommendations
- **Tools:** 8–10 tools with distinct verbs. Each description has 3–6 sentences, says "use X instead when…" and includes an example call.
- **Envelope:**
  - evidence_id, tool, sql, params, data_scope, total_matches, returned, truncated, query_ms, next_cursor, notes[], columns/rows
  - search cap 100 (default 20), context ≤50 per side, trend ≤200 buckets, group ≤50 plus "other"
- **SQL safety:**
  - Fixed SQL templates with enum-allowlisted identifiers. No raw-SQL tool.
  - Read-only transaction plus `cancel_safe()` timeout.
- **Transport:**
  - stdio with a committed `.mcp.json` (uv run, `${DATABASE_URL:-...}`, alwaysLoad).
  - Claude Desktop config snippet, optional .mcpb bundle, optional streamable-http.
- **Prompts:** engineer / team_lead / manager.
- **Resources:** schema, ownership, templates, evidence. Mirror them in tools.
