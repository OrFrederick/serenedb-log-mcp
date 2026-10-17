## Research: ugnmura's Codex

**Recommendation:** Keep six essential capabilities—`describe_corpus`, `search_logs`, `get_context`, `count_by`, `trend`, `get_evidence`—and add distinct `top_patterns`, `latency_stats`, `compare_periods`, `group_entries` capabilities as their contracts become available. The ten-tool candidate has generated input/output schemas, but its selection accuracy versus six tools is unmeasured. Use the official **mcp==2.3.0 `MCPServer`**, stdio for local judging and optional Streamable HTTP with the same handlers. Separate database read privileges from optional index-administration credentials. Resolve aggregate truncation/count semantics explicitly before finalizing the Evidence schema.

**Confidence:** high in measured SQL/coverage and tested SDK compatibility; medium in final granularity and proposed limits until live Role evaluations. This is a schema/SQL research prototype, not a completed product server.

### Options compared

| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| Six essential tools plus typed analytic additions | Recommended candidate | Clear search/count/context boundaries; exposes useful measured capabilities | Ten-versus-six accuracy unmeasured | [Tool table, full schemas and experiments](https://github.com/OrFrederick/serenedb-log-mcp/tree/research/2-ugnmura/research/2/ugnmura) |
| One broad SQL tool | General database work | Flexible | Open-ended SQL unnecessary; dialect, cost and Role errors become agent responsibilities | [ClickHouse](https://github.com/ClickHouse/mcp-clickhouse), [Postgres MCP Pro](https://github.com/crystaldba/postgres-mcp) |
| Consolidated `analyze(action, options)` | Worth A/B testing | Fewer definitions | Mode/argument ambiguity may replace tool-choice errors | [Anthropic guidance](https://www.anthropic.com/engineering/writing-tools-for-agents); no local model result |
| Official SDK2.3.0 | Settled-stack fit | Actual legacy/2026-07-28 probes passed | API differs from old FastMCP examples | [Release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), issue#3 artifacts |
| Standalone FastMCP | Separate framework option | Additional framework features | Currently4.1.0, not issue's2.x; unnecessary package/API migration | [Release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0) |

### Experiments run

On local **SereneDB26.10.1**, using docs-MCP-verified SQL and a throwaway research loader:

- Whole Base Corpus: **4,747,963 BGL +207,820 OpenStack =4,955,783 Log Entries**. Exact hashes, schema and parser version are retained. Empty/continuation/NUL messages are preserved; `probe_nul.py` demonstrates exact COPY round-trip where psycopg write_row rejects NUL.
- Full inverted index, refresh, BM25/highlighting, term/phrase/fuzzy count+bounded page: OpenStack **2,066** `build instance` phrase matches; BGL **40,026** `machine check` phrase matches; BGL `error` and distance1 typo `erorr` each **557,845** matches. Zero-match and bound injection-shaped inputs behaved correctly. Index build31.682s on a contended, emulated local environment; no production speed claim.
- Exact counts, trends, instance context, HTTP/build quantiles, period comparison and lag/window grouping ran. Strict template mapping + SereneDB joins retained all **4,955,783** entries, with zero duplicate map keys. BGL **4,630,794 unique assignments,161 ambiguous,34,470 empty,82,538 other unmatched**; OpenStack **207,632 assigned,188 unmatched**. Independent issue#6 raw classification agrees.
- SELECT-only reader INSERT denied42501. Aggregate-view-only Manager read succeeded; raw-table SELECT denied42501. This verifies the tested grants boundary, not universal security/RLS.
- **Failure preserved:** `statement_timeout='100ms'` was accepted/SHOW confirmed, yet `pg_sleep(5)` completed after5.029s. A follow-up reproduced the issue with both identities. `probe_sql.py` intentionally fails that assertion. CPU-bound timeout/driver cancellation remain unverified; do not claim a hard resource bound from the setting alone.

[Executed SQL, parameters, results, schema generator, security follow-up and reproduction instructions](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/2-ugnmura/research/2/ugnmura/report.md). Timings are shared-machine observations. The supplied loader/schema/Ownership Table remains unavailable; no guessed Team assignments.

### Sources

[MCP tools2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [official migration](https://py.sdk.modelcontextprotocol.io/migration/); [SereneDB inverted indexes](https://serenedb.com/docs/sql/statements/create_index/inverted), [full-text functions](https://serenedb.com/docs/sql/functions/search/full-text), [GRANT](https://serenedb.com/docs/sql/statements/grant), [configuration](https://serenedb.com/docs/configuration/overview). [Independent specialist audit](https://github.com/OrFrederick/serenedb-log-mcp/blob/research/2-ugnmura/research/2/ugnmura/tool-specialist.md) covers current Grafana/Datadog/ClickHouse/Postgres/Axiom/Elastic interfaces and primary empirical papers. General tool-library studies do not establish our optimal tool count.

### Risks / unknowns

The glossary compares returned rows with matching Log Entries; complete aggregations intentionally return fewer rows. Proposed additions: explicit source count, result-row unit/count, result truncation and shortened-content flags. **Human clarification is required; glossary unchanged.** Count/page must share a validated snapshot or immutable corpus/index version. Pagination is our application contract, not automatic tools/call pagination. Every SQL/parameter pair and query timing must be retained; failed counts are not zero. Normalize Decimal and large integers deliberately.

Role prompts are guidance; authenticated identity/grants enforce access. Saved Evidence/resources/cursors also require authorization. `get_evidence` retrieves immutable history; replay creates a new comparison. Because every call appends Evidence, conservative draft annotations mark additive writes/non-idempotence while database access remains SELECT-only; document any alternative incidental-audit interpretation. Whole-corpus aggregation must remain possible; response byte budgets, query cancellation and live Claude tool selection still need integration tests.

### Open questions for the humans

Approve clarified aggregate truncation/count wording? Choose the six-versus-ten held-out eval winner before freezing names. Confirm supported client versions and the scope of optional admin extensions. Reconcile the installed timeout behavior before claiming enforced execution deadlines.
