## Decision proposal

**Chosen option:** 12 intent-level, read-only tools plus 2 stretch tools, on `mcp` 2.3.0 `MCPServer`.

**Core tools:**
1. `describe_corpus`
2. `search_logs`: sort by time by default; returns rows plus an exact total and facets.
3. `get_context`: same node, same request or same instance, where the instance scope also matches the UUID in the message.
4. `count_by`: optional baseline period; returns `distinct_nodes`, `first_seen`/`last_seen` and an `(other)` row.
5. `trend`: zero-filled, at most 400 buckets.
6. `top_patterns`: uses the precomputed Pattern column from #6.
7. `list_incidents`: `filter`, `pattern_key`, `gap_s`, `merge_scope` and `sort`, as #1 defined them.
8. `latency_stats`
9. `get_evidence`

**Settled differentiators:**

10. `verify_answer`
11. `export_investigation`
12. `find_precursors`

**Stretch:** `create_index` and `drop_index`, the only tools that are not read-only.

**How we build them:**
- **Results:** every result goes in `structuredContent` as the Evidence envelope, in a `columns` + `rows` layout with `notes`. The text block mirrors it.
- **Errors:** raised as `ToolError` with an actionable message.
- **Loading:** every tool sets `_meta["anthropic/alwaysLoad"]`.
- **Queries:** all SQL comes from fixed templates and runs through psycopg `ClientCursor` with `conn.read_only=True` and a client-side `cancel_safe()` timeout.
- **Filters and lookups:** in relevance mode, filters use `::score(NULL)`. Point lookups never go through the inverted index.

**Limits:**

| Tool | Default | Maximum |
|---|---|---|
| `search_logs` rows | 20 | 50 |
| `count_by` groups | 25 | 100 |
| `get_context` entries per side | 10 | 50 |

**Rationale:** Both Claudes converged on the core set once the duplicate `compare_periods` was folded into `count_by` and `list_incidents` was added for #1's Incident model. Every remaining dispute was settled by measurement.
- Claude Code passes only `structuredContent` on success and only text on `isError` (probe on 2.1.263). This fixes the envelope's channel.
- Plain SQL filters add +2.0 to BM25 scores, while `::score(NULL)` does not. This fixes the filter form.
- The regex Pattern fallback takes 1.4–13 s over BGL and yields 18,846 pseudo-patterns against 320 real templates. This forces the precomputed Pattern column.
- Adding a `line_no` predicate makes the inverted index ignore the `ts` filter. This forces point lookups onto the base table.

`verify_answer`, `export_investigation` and precursor mining are added because decisions.md already settles them as differentiators and neither research comment covered them. Twelve tools keep each Role's path to 3–6 calls and stay far below the tool counts where selection is reported to degrade.

**Rejected options:**
- **A separate `compare_periods` tool:** it overlaps with `count_by`, which is the selection risk Anthropic names.
- **A raw-SQL tool:** a SELECT-only role can still `read_csv('/etc/passwd')`, `SET` is partly global, and `statement_timeout` is not enforced.
- **Three or four broad tools:** the parameter combinations get complex, and judges find them harder to read.
- **A self-sufficient Markdown text block:** Claude Code drops text blocks when `structuredContent` is present.
- **Regex Patterns at query time:** 1.4–13 s per call, and they over-split Patterns 59×.
- **Standalone `fastmcp` 4.1:** 66 dependencies, it leaks exception text by default, and it is not the SDK decisions.md names.

**Decisions the humans must make:**
1. Confirm `verify_answer`, `export_investigation` and `find_precursors` as tools, and their names.
2. Find out which SereneDB version the organisers run. It decides the dictionary DDL fallback and the SIGSEGV risk, which appeared on 26.10.1 only.
3. Report the SereneDB findings to the organisers:
   - `line_no` makes the index drop the `ts` filter;
   - a SELECT-only role can read files;
   - some `SET`s from an unprivileged role are global.
4. Decide whether to prefix tool names (`logs_*`), and whether to host HTTP for judges.
