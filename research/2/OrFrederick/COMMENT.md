## Research: OrFrederick's Claude

**Recommendation:** Ship **8 read-only tools**. Each tool is intent-level and returns a shared Evidence envelope:

1. `describe_corpus`
2. `search_logs`
3. `get_context`
4. `count_by`
5. `trend`
6. `top_patterns`
7. `latency_stats`
8. `get_evidence`

Add 2 stretch tools for the index bonus: `create_index` and `drop_index`. This changes the draft in three ways:

- **`compare_periods` becomes optional `baseline_start`/`baseline_end` inputs on `count_by`.** One aggregation tool means one obvious choice, and the SQL is the same `count(*) FILTER` query.
- **`search_logs` always returns the exact total match count plus a one-pass facet summary** (distinct nodes, racks, first and last seen, top Components and levels). A sample of lines then never stands alone, which is what the brief means by "a few sample lines do not show how often something occurs".
- **`count_by` and `trend` always return `distinct_nodes` and `first_seen`/`last_seen`,** because the data shows why. One node, `R30-M0-N9-C:J16-U01`, produced 152,229 FATAL/FAILURE entries in 36 hours, which is 17.8% of all FATAL/FAILURE entries in BGL. Raw counts alone would mislead every Role.

Implementation choices:

- **SDK:** official `mcp==2.3.0` (`MCPServer`, formerly FastMCP).
- **Transport:** stdio as the documented path for Claude Code and Desktop, with a `--http` flag for Streamable HTTP. Same code, one flag.
- **Prompts:** the three Roles become MCP prompts (`engineer`, `team_lead`, `manager`).
- **Resources:** schema, Ownership Table, templates, glossary and Evidence.

Every tool SQL below ran on the real Base Corpus at **2–150 ms warm**.

**Confidence:** **High** for the tool set, the SQL and the limits, which are measured on our data, plus the SDK choice, which was installed and run. **Medium** for exact description wording and default limits, which need the tool-selection eval (issue: eval harness). **Medium** for the safety story, because SereneDB 26.09.1.3 has real gaps; see Risks.

### Tool table

All tools share these properties:

- **Annotations:** `readOnlyHint: true`, `idempotentHint: true`, `openWorldHint: false`, plus a `title`.
- **Shared inputs:** `dataset` (`bgl` | `openstack`, plus `all` where it makes sense), `start`, `end` (ISO-8601, end-exclusive), `component`, `level` (list), and for BGL `node`/`rack`, for OpenStack `request_id`/`instance_id`.
- **Shared output (Evidence envelope):**
  - `evidence_id` (E1, E2, ...)
  - `sql`, `params`, `data_scope`
  - `total_matches`, `returned`, `truncated`
  - `query_ms`
  - `hint`: how to narrow the query, or what to call next
  - `rows`

| Tool | Purpose (description gist) | Key inputs | Output rows | Limits | SQL core (verified) | Warm ms |
|---|---|---|---|---|---|---|
| `describe_corpus` | **Call first.** Lists Datasets, row counts, time ranges, columns, Components, levels, Ownership Table coverage, index status and query syntax. | none | one block per Dataset | fixed, about 1k tokens | `count(*)`, `min/max(ts)`, `count(DISTINCT component)` | 10–800 (cache it at startup) |
| `search_logs` | Full-text search for Log Entries, with time and field filters. Returns matching lines **and** how many match in total. Use it to find *which* lines; use `count_by`/`trend` for *how often*. | `query` (web-search syntax: words are ANDed, `"phrase"`, `OR`, `-exclude`), `fuzzy` (0–2, single term), `sort` (`time` default \| `relevance`), `limit` | `entry_id` (`bgl:827110`), ts, node/instance, Component, level, highlighted `snippet` (300 chars or fewer); plus `facets` | limit default 20, max 100 | `FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery($q) AND ts >= $s AND ts < $e [AND component=$c AND list_contains($levels::text[], level)] ORDER BY ts LIMIT $n`, plus a separate `count(*)` and facet query | 5–60 (fuzzy 131) |
| `get_context` | Shows what happened just before and after one Log Entry. | `entry_id`, `scope` (`same_node` \| `same_rack` \| `whole_dataset` \| `same_request` \| `same_instance`), `before`/`after`, `window` | ordered entries, with the anchor marked | before/after default 10, max 50; window default 1h, max 24h | two `UNION ALL` halves: `node=$n AND ts<=anchor AND ts>=anchor-$w ORDER BY ts DESC LIMIT $k` and the mirror half | 2–7 |
| `count_by` | Counts matching Log Entries grouped by one dimension over a stated period. Optional baseline period for comparisons. Counts entries, **not incidents**. | `group_by` (`component`, `level`, `node`, `rack`, `midplane`, `team`, `dataset`, `alert_label`, `http_status`, `instance_id`), optional `query`, `top_n`, `baseline_start`/`baseline_end` | key, n, share, distinct_nodes, first_seen, last_seen [, baseline_n, delta, ratio]; plus `(other)` row and `groups_total` | top_n default 15, max 50; the rest is rolled into `(other)`, so totals stay exact | `GROUP BY` with `row_number()` and an `(other)` bucket; baseline uses `count(*) FILTER (WHERE ts in A)` and `FILTER (WHERE ts in B)` | 5–30 |
| `trend` | Counts per time bucket, zero-filled, optionally split by a dimension. Use it for "is this getting worse", bursts and recurrence. | `bucket` (`auto` \| `hour` \| `day` \| `week`), optional `query`, `split_by` (top 5 groups) | bucket, n, distinct_nodes; plus peak bucket and non-zero bucket count | at most 200 buckets (`auto` picks the width) | `time_bucket($w::interval, ts, $start)` left-joined to `generate_series` | 6–22 |
| `top_patterns` | Recurring message templates and how often each occurs. | filters, `top_n` | template_id, template, n, distinct_nodes, Components, first_seen/last_seen, example `entry_id` | default 20, max 50 | `GROUP BY template_id` (needs the parsing-issue column); fallback is a `regexp_replace` mask | 8–132 (regex fallback) |
| `latency_stats` | OpenStack request latency percentiles, by group or over time. | `group_by` (`path_template`, `http_method`, `http_status`, `source_file`, `instance_id`, `none`), `bucket` | n, p50, p95, p99, max | groups max 50 | `quantile_cont(latency_s, [0.5,0.95,0.99])` (`percentile_cont ... WITHIN GROUP` also works) | 4–11 |
| `get_evidence` | Returns or re-runs a stored Evidence entry, so the agent can cite or verify a number. | `evidence_id`, `rerun` (bool) | the stored envelope [+ `rerun_matches: true/false`] | — | replays the stored SQL and parameters | as the original |
| *`create_index`* (stretch) | Builds an inverted index over a time slice. `destructiveHint: false`, not read-only. | dataset, start, end | name, rows, `build_ms` | max N live indexes; auto-drop TTL | `CREATE INDEX ... ON <slice view> USING inverted (...)` | 8,107 for all of BGL |
| *`drop_index`* (stretch) | Drops an agent-created index. `destructiveHint: true`. | name | — | only `agent_` prefixed indexes | `DROP INDEX` | — |

**Why 8 tools, and not 4 or 12.** Anthropic reports that tool selection degrades past 30–50 tools, and tool search is only advised at 10 or more. OpenAI's soft guidance is fewer than 20. Every observability MCP we surveyed converges on the same set: orientation, search, aggregate, patterns. Grafana Loki exposes query, stats, patterns and labels; Datadog has search plus analyze; Honeycomb has `get_workspace_context` first, then `run_query` and BubbleUp.

So the number is not the risk. The risk is **overlap**, which Anthropic's tool-writing post calls out explicitly. That is why `compare_periods` was folded into `count_by`, and why each description names its sibling tool ("for how often, use count_by").

**Descriptions.** Each description is 3–6 factual sentences, with the critical words first. Claude Code cuts upfront descriptions and server instructions at 4,096 characters. Each description covers:

- what the tool returns;
- when *not* to use it;
- input formats, with one example;
- defaults and caps.

We add no promotional wording: one study found a single promotional sentence raised a tool's usage more than 7×, and the Claude directory rejects it.

**Output format.** The text block is self-sufficient: a first line `E7 · bgl · 2005-06-01..2006-02-01 · 5,983 matches, 20 shown (TRUNCATED)` followed by a compact Markdown table. `structuredContent` with `outputSchema` holds the same data. Reasons:

- Claude Code has had bugs where only one of the two reached the model.
- On `isError`, Claude Code drops `structuredContent` and shows only the text.
- The SDK's default text is pretty-printed JSON, about 2–3× the characters. We override it with `Annotated[CallToolResult, Model]`, which was verified.

The token target is under 4k per result. Claude Code warns at 10k and caps at 25k.

**Errors.** Errors are `isError` results whose text names the bad field, the valid values and one corrected example. For instance: "level must be a list of BGL levels: FATAL, ERROR, ...; e.g. ["FATAL"]". Zero matches is **not** an error; it returns a hint instead ("0 matches; try fuzzy=1 or drop the component filter").

### Role prompts (MCP prompts; single-token string arguments, because Claude Code splits prompt arguments on whitespace)

- **`engineer(question)`**
  - Workflow: `describe_corpus` → `search_logs` → `get_context` on 1–3 anchors → `count_by` node or Component to judge spread.
  - Output: Observed Facts with `[E#]`, Hypotheses (possible cause), Unknowns, Next Step = a concrete check (a query or a node to inspect).
- **`team_lead(period)`**
  - Workflow: `count_by` Team/Component → `trend` → `top_patterns` → `count_by` with baseline.
  - Output: recurrence over the stated period, which Teams are affected, what to investigate first, and each count's Data Scope.
- **`manager(period)`**
  - Workflow: whole-Base-Corpus `count_by` dataset/Team → `trend` → at most 3 prioritised items.
  - Constraints: only measured frequency and affected Components. Never mention financial loss, downtime or customer impact. Matching entries are not incidents.

### Resources

| URI | Contents |
|---|---|
| `logs://schema` | Columns and meanings per Dataset |
| `logs://ownership` | The Ownership Table |
| `logs://templates/{dataset}` | Template list |
| `logs://glossary` | Canonical terms |
| `logs://query-syntax` | Supported query syntax |
| `evidence://{id}` | Stored Evidence |

Resources help users, through `@` mentions in Claude Code and the + menu in Desktop. They do not replace tools, because the model in Desktop cannot pull resources itself. That is why `describe_corpus` and `get_evidence` exist as tools as well.

### Options compared
| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| **8 intent-level tools (recommended)** | High | No overlap; each Role's path is 3–5 calls; every result is an Evidence envelope | Some tools have many parameters | Tool-count guidance; survey of Grafana, Datadog and Honeycomb; benchmark below |
| Draft's 9 tools (separate `compare_periods`) | Medium | Explicit | `count_by` and `compare_periods` overlap, a selection risk | Anthropic "consolidate tools" |
| 3 tools: search, aggregate, raw SQL | Low | Flexible | The brief says open SQL is not required; the agent writes SereneDB SQL, which differs from Postgres (see the dictionary syntax below); unsafe given the file-access gap | The judges' "sensible limits" criterion |
| Official `mcp` 2.3.0 `MCPServer` | High | Matches the decision; 28 packages; masks crashes by default; in-memory `Client(server)` for pytest | v2 import paths (`mcp.server.fastmcp` is gone) | Installed and run |
| `fastmcp` 4.1.0 | Medium | CLI `install claude-code`, middleware | 66 packages; leaks exception text by default | Installed and run |
| stdio + `--http` flag | High | Claude Code and Desktop config just work; HTTP for remote judges | HTTP needs host and DNS-rebinding settings | SDK test; MCP 2026-07-28 spec |

### Experiments run
Code and logs: https://github.com/OrFrederick/serenedb-log-mcp/tree/research/2-OrFrederick/research/2/OrFrederick (`bench.py` and `bench_results.json` hold all SQL with timings; `*_probe.py` and `*.log` hold the probes; `sdk/` holds the SDK scripts).

**Index and search**
- **Indexes (my own, over views).** `r2_bgl_fts` is on view `r2_bgl_v`: 4,747,963 rows, built in **8.1 s**. `r2_os_fts` is on view `r2_os_v`: 207,820 rows, built in 1.9 s. Base tables were not altered. The indexes cover columns `line_no, ts, node, rack, component, level, alert_label, content` and use a dictionary with frequency, position, offset and norm.
- **Index vs scan.**
  - "parity": index **3 ms** vs `ILIKE` scan **84 ms** (121,498 token matches vs 121,512 substring matches).
  - Phrase "failed to read message prefix": **3 ms** vs **78 ms** (5,983 = 5,983).
- **Search syntax.** `websearch_to_tsquery` ANDs words and handles phrases, OR and `-exclude`, and **never raises a parse error** (malformed input gives 0 rows). `to_tsquery` raises errors on bad input. A bare string ORs its terms: `'ciod failed'` gives 385,562 matches vs 50,918 with AND, which is a trap the tool must hide.
- **Fuzzy.** `ts_levenshtein('instrucion',1)` matched 202,310 in 131 ms, and `ts_highlight` marked "instruction".
- **Total count strategy.** `count(*) OVER ()` costs **34 ms** on a broad query ("error", 557,845 matches) vs **5 + 2 ms** for top-20 plus a separate `count(*)`. **Use a separate count.**
- **Facets.** The one-pass facet summary takes 15 ms; `GROUPING SETS` facets take 24 ms.
- **BM25 ranking** of near-identical log lines gives arbitrary order: identical messages scored between 171 and 503. **Default `sort=time`**, with relevance as an option.

**Context and aggregation**
- **`get_context`** same-node ±1h takes 4 ms on the base table and 16 ms through the index. By request_id it takes 7 ms, by instance_id 2 ms.
- **Aggregations.** `count_by` with the `(other)` bucket takes 30 ms, compare-periods 5 ms, `trend` zero-filled 6–20 ms, `top_patterns` regex fallback 132 ms (BGL) and 8 ms (OpenStack). Latency percentiles take 4–11 ms. Whole-Base-Corpus counts by dataset and level take 13 ms.
- **Parameter binding.** psycopg 3 server-side binding works for `@@`, `LIMIT` and intervals. `= ANY(%s)` with a Python list **fails** ("UNNEST not supported here"); use `list_contains(%s::text[], col)`, which takes 7 ms. The injection string `x') or 1=1 --` was treated as search text.

**Safety probes**
- **`statement_timeout`** is accepted but **not enforced**: a query with a 1000 ms timeout ran for 13.3 s. A client-side `conn.cancel_safe()` returns `QueryCanceled` after about 0.3 s, and the connection stays usable.
- **Read-only role.** `CREATE ROLE ... LOGIN` plus `GRANT SELECT` blocks INSERT, CREATE and DROP (verified). However, the role **could still run `read_csv('/etc/passwd')` and `COPY ... TO` a file.**
- **`SET` from that unprivileged role is server-global.** Its `SET enable_external_access=false; SET lock_configuration=true` locked configuration for every session, including the superuser, until a container restart. This happened to the shared instance during this research. It is restored, and the row counts were checked: 4,747,963 and 207,820.

**SDK**
- `mcp` 2.3.0 and `fastmcp` 4.1.0 were both installed. In each, a server with a Pydantic-returning tool, a prompt and a resource was called in-process and over Streamable HTTP. Both return `outputSchema`, `structuredContent` and `isError` correctly; both pytest suites pass 2/2.

### Sources
- Anthropic, Writing effective tools for agents: https://www.anthropic.com/engineering/writing-tools-for-agents
- Anthropic, Advanced tool use (tool search accuracy 49%→74%): https://www.anthropic.com/engineering/advanced-tool-use
- Anthropic, Code execution with MCP: https://www.anthropic.com/engineering/code-execution-with-mcp
- Claude docs, Define tools: https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- Claude docs, Tool search tool (30–50 tool threshold): https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool
- Claude Code MCP docs (25k-token cap, 4,096-character descriptions, prompts as slash commands): https://code.claude.com/docs/en/mcp
- Claude connector review criteria: https://claude.com/docs/connectors/building/review-criteria
- MCP spec, tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP spec, 2026-07-28 changelog: https://modelcontextprotocol.io/specification/2026-07-28/changelog
- MCP spec, pagination (list operations only): https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination
- Python SDK v2 migration guide: https://py.sdk.modelcontextprotocol.io/v2/migration/
- Python SDK releases: https://github.com/modelcontextprotocol/python-sdk/releases
- PyPI: https://pypi.org/pypi/fastmcp/json
- Server survey:
  - https://github.com/grafana/mcp-grafana (Loki: default limit 10, max 100, limit+1 truncation detection)
  - https://docs.datadoghq.com/mcp_server/tools/
  - https://github.com/ClickHouse/mcp-clickhouse (readonly=1, 30 s timeout)
  - https://github.com/crystaldba/postgres-mcp
  - https://docs.honeycomb.io/integrations/mcp/tools.md
  - https://axiom.co/docs/console/intelligence/mcp-server
  - https://github.com/elastic/mcp-server-elasticsearch
- Papers on tool selection and descriptions:
  - RAG-MCP: https://arxiv.org/abs/2505.03275
  - MCP Tool Descriptions Are Smelly: https://arxiv.org/abs/2602.14878
  - From Docs to Descriptions: https://arxiv.org/abs/2602.18914
  - Tool preferences are unreliable: https://arxiv.org/abs/2505.18135
  - How many tools should an agent see: https://arxiv.org/abs/2605.24660
  - LiveMCPBench: https://arxiv.org/abs/2508.01780
- SereneDB docs, via the docs MCP:
  - https://serenedb.com/docs/sql/statements/create_index/inverted
  - https://serenedb.com/docs/sql/functions/search/full-text
  - https://serenedb.com/docs/sql/functions/search/highlighting
  - https://serenedb.com/docs/configuration/overview

### Risks / unknowns
- **Docs and the Docker image disagree.** On 26.09.1.3, the documented `CREATE TEXT SEARCH DICTIONARY d AS split_text(...) WITH (...)` is a **syntax error**. The legacy form works: `(TEMPLATE='text', locale='en_US.UTF-8', "case"='lower', stemming=false, accent=false, frequency=true, position=true, "offset"=true, norm=true)`. The organisers' instance may run either version, so our DDL needs a fallback, or we must confirm the version on day 1.
- **Safety gaps.** There is no enforced `statement_timeout`, a SELECT-only role can still read and write files, and an unprivileged `SET` is global. Mitigation:
  1. Expose no raw-SQL tool.
  2. All SQL lives in server templates with bound parameters and enum-checked identifiers.
  3. A client-side cancel after 10 s.
  4. The server never issues `SET`.
  5. Use a LOGIN role with SELECT only.

  We should report the last two findings to SereneDB.
- **`top_patterns` depends on a `template_id` column** from the parsing issue. The regex fallback works, but it over-splits hex IDs.
- **`team` grouping depends on the Ownership Table,** which has not arrived yet. Components missing from the table must show as `(unowned)`, never be silently dropped.
- **Unverified client behaviour:** whether Claude Desktop surfaces MCP prompts in its menu, and whether the current Claude Code passes both text and `structuredContent` to the model. The self-sufficient text block covers both cases.
- My indexes `r2_bgl_fts` and `r2_os_fts` remain on the shared instance for re-runs; another agent has built `r4_bgl_idx`. Should we drop mine after deliberation?

### Open questions for the humans
1. Do we accept folding `compare_periods` into `count_by`, or keep it as a separate tool for demo clarity?
2. Should the product index the base table directly (needs a primary key; is the official loader's schema known?) or a view (works now; it is a snapshot that needs `REINDEX` after a reload)?
3. Prefix the tool names (`logs_search`, ...)? It only matters if judges run several MCP servers at once.
4. Do we ship Streamable HTTP for judges (needs a hosting decision), or document stdio only?
5. Which SereneDB version does the organisers' instance run? This decides the DDL syntax.
