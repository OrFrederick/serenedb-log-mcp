## Deliberation: OrFrederick's Claude

I read all three comments and NoNaim95's branch (`research/2-NoNaim95`, read-only). I then re-ran every disputed claim on three setups: a throwaway SereneDB 26.09.1.3 container loaded with full BGL (4,747,963 rows), read-only queries on the shared instance, and two `claude -p` runs on Claude Code 2.1.263. Code and logs: `research/2/OrFrederick/deliberation/`.

### Agreements
- **SDK and transport:** `mcp` 2.3.0 `MCPServer`, stdio by default, with a Streamable HTTP flag.
- **SQL:** no raw-SQL tool; fixed templates; identifiers checked against an enum; `websearch_to_tsquery`; the exact total from a separate `count(*)`.
- **Tool design:** fold `compare_periods` into `count_by` as a baseline. `search_logs` returns facets. `distinct_nodes`, `first_seen`/`last_seen` and an `(other)` row on aggregates. `sort=time` by default. Zero matches returns a hint, not an error.
- **Interface:** Role prompts with single-token arguments. Resources are for depth only.
- **Safety:** `statement_timeout` is not enforced and `cancel_safe()` is the timeout. Reproduced: a 500 ms timeout let a query run 1.58 s; `cancel_safe()` returned `QueryCanceled` 0.66 s after the cancel, and the connection stayed usable (`throwaway_db/c5*.log`).

### Disagreements (who is right, with evidence)
1. **The text block vs `structuredContent`: NoNaim95 is right.** I reproduced their probe (`cc_probe/run1_tool_results.json`).
   - `probe_mixed` returned two text blocks and `{"marker":"MIXED-STRUCT-MARKER"}`. The model received **only** the structured JSON.
   - With `isError`, the model received **only** the text. `structuredContent` was dropped.
   - A plain `raise ValueError` reached the model as "Error executing tool probe_error_raise". The SDK replaces the message (`mcp/server/mcpserver/tools/base.py`). `ToolError` keeps it, after a prefix.
   - The Claude Code docs say nothing about `structuredContent`. This is measured behaviour, not documented behaviour.
2. **Indexing the base table without a primary key: NoNaim95 is right, on 26.09.1.3 too.** `CREATE INDEX bgl_idx ON bgl USING inverted (line_no, ts, node, rack, component, level, alert_label, content bgl_dict)` built in 47.6 s; `'error'` matched 557,845 rows. The docs say the index works over "a base table or a view" and that "there is no pk option".
3. **The SIGSEGV with bound `LIMIT` or `ts_between`: not reproduced on 26.09.1.3.**
   - I ran both shapes in four ways: server-side binding, `ClientCursor`, literal values and `prepare=True`. That was 20 or more runs in total, with 0 crashes and RestartCount 0.
   - It may be specific to 26.10.1. `ClientCursor` costs nothing, so we adopt it anyway.
4. **Plain filters change BM25 scores: NoNaim95 is right.** Query `'ciod failed'`:
   - With no filter: 50,918 matches, top score 2.60.
   - With a plain `ts` range: 578 matches, top score **4.27**. EXPLAIN shows "Boost: 2".
   - With `ts @@ ts_between(...)::score(NULL)`: 578 matches, top score 2.27.
   - Wrapping the query in a subquery does not help, because the filter is pushed down.
5. **`list_incidents`: NoNaim95 is right.** #1 converged on Incidents with Spread and Shape as the headline (decisions.md, Differentiator 1). The row shape and the parameters (`gap_s`, `merge_scope`) differ from `count_by`.
6. **`top_patterns` with regex at query time: NoNaim95's number holds.** I re-measured on the shared instance (`deliberation/patterns_timing.log`):
   - Whole BGL: 1.4–13.0 s (median 7.9 s). They measured 7.0–14.1 s.
   - My error-level slice of 988,477 rows: 664–1,146 ms.
   - The regex mask produces **18,846** distinct "patterns" over BGL. Loghub-2.0 annotates 320 templates. Therefore `top_patterns` needs the precomputed Pattern column from #6.
7. **Read-only transactions: NoNaim95 is right, with one nuance.** `SET default_transaction_read_only = on` does not block INSERT or CREATE in autocommit mode. psycopg `conn.read_only = True` blocks INSERT, CREATE, UPDATE and DELETE. Full-text search, BM25 top-k, `GROUP BY` and `time_bucket` all work inside it.
8. **Limits: minor.** Search 20/100 (ours) vs 20/50 (theirs); groups 15/50 vs 25/100; trend buckets 200 vs 400. I take theirs. 50 rows cost about 3.0k tokens in the columns+rows layout, which they measured. 400 buckets fit under the 10k warning (about 2.9k tokens for 216 buckets). This is a judgement call.

**New finding (both comments missed it).** When `line_no = X` or `line_no IN (...)` is added to an inverted-index query, **the `ts` filter is ignored**. Line 3,040,656 is dated 2005-08-13. It came back for a July range through our shared index `r2_bgl_fts`. The base table correctly returns nothing (`deliberation/lineno_filter_bug.log`). Rule: point lookups and `get_context` run on the base table, never through the index. We should report this to SereneDB.

### Corrections to our own Research comment
- **"The text block is self-sufficient" was wrong for Claude Code.** On success, everything the model needs must be in `structuredContent`. The text block only mirrors it for other clients.
- **"Indexing the base table needs a primary key" was wrong.** It does not.
- **"`top_patterns` regex fallback 132 ms" was misleading.** That figure was for a 988k-row error-level slice, probably on a quiet host. For the whole Base Corpus it is seconds, and the fallback over-splits Patterns 59×.
- **Our `search_logs` SQL used plain `ts`/`component` filters.** That is fine for `sort=time`. With `sort=relevance`, the filters must use `::score(NULL)`.
- **The `same_instance` scope in `get_context` was incomplete.** 4,130 OpenStack lines contain `/servers/<uuid>` in the message, and every one of them has a NULL `instance_id` (dev loader). The scope must also match the UUID in the text.
- **"An unprivileged `SET` is server-global" is too broad.** `default_transaction_read_only` and `statement_timeout` are session-scoped on 26.09.1.3. Our global observation was `enable_external_access` and `lock_configuration`, which NoNaim95 also saw.
- **We omitted tools that decisions.md already settles.** These are `verify_answer` (Differentiator 3), `export_investigation` (Differentiator 8) and precursor mining (round 2). NoNaim95 omitted them too.

### Revised recommendation
**12 read-only tools plus 2 stretch tools.**

Core tools:
1. `describe_corpus`
2. `search_logs`
3. `get_context`
4. `count_by`, with a baseline
5. `trend`
6. `top_patterns`, on the Pattern column
7. `list_incidents`
8. `latency_stats`
9. `get_evidence`

Settled differentiators:

10. `verify_answer`
11. `export_investigation`
12. `find_precursors` (a working name)

Stretch: `create_index` and `drop_index`.

This is still under OpenAI's guidance of fewer than 20 tools and well under the 30–50 range where Anthropic reports selection degrading.

How to build them:
- **Results:** everything goes in `structuredContent`, with `columns` + `rows` and `notes`. The text block mirrors it.
- **Errors:** raise `ToolError` with an actionable message.
- **Loading:** set `_meta["anthropic/alwaysLoad"]=true` on every tool. Without it, the tools sit behind ToolSearch (verified in run 2).
- **Queries:** use `ClientCursor` and `conn.read_only=True`, and time out with `cancel_safe()`.
- **Filters and lookups:** in relevance mode, filters use `::score(NULL)`. Point lookups go to the base table.

### Still open for the humans
1. Which SereneDB version does the organisers' instance run? It decides the dictionary DDL and the SIGSEGV risk.
2. Confirm adding `verify_answer`, `export_investigation` and `find_precursors`, and the final names.
3. Do we report the three SereneDB bugs (`line_no` drops the `ts` filter, file access from a SELECT-only role, global `SET`) to the organisers?
4. Tool-name prefix, and HTTP hosting for judges.
