# MCP tool design: independent Codex research

Researcher: ugnmura's Codex, 2026-10-17. This is a research recommendation, not a confirmed decision. No other Research comments were read before this report. Companion `tool-specialist.md` is an independent protocol/interface audit.

## Recommendation

Use the official Python SDK, pinned to **mcp==2.3.0**, with `from mcp.server import MCPServer`, psycopg 3, stdio for local judging, and the same handlers optionally exposed through Streamable HTTP. Start from six necessary capabilities (describe, search, context, counts, trends, Evidence lookup), then expose patterns, explicitly typed durations, period comparison, and candidate grouping as distinct tools when available. The ten-tool candidate below has concrete schemas; its superiority over six tools or consolidated analytics is **unmeasured**. Select the final granularity with issue #3's held-out Role evaluations.

The current official package renamed FastMCP to MCPServer; standalone FastMCP is a different package, currently 4.1.0. The settled official-SDK choice remains appropriate. Primary sources: [official release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), [migration](https://py.sdk.modelcontextprotocol.io/migration/), [standalone release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0). Issue #3's actual in-process and subprocess/stdio probes passed legacy and 2026-07-28 discovery, prompt retrieval, schemas, calls and validation. No live Claude selection or Desktop UI compatibility was measured.

## Tool table and schemas

`contracts.py` generates `tool-contracts.json`: ten input schemas, typed row schemas, successful Evidence envelopes and explicit failure shapes. These are **draft contracts**, not ten implemented product handlers. Required shared scope selects BGL/OpenStack, either explicit half-open bounds or full coverage, source files, Components, levels and labels. Research timestamps explicitly use source-printed naive values; the official loader's time semantics must replace this declaration before product use. Values are bound; dimensions, operators, bucket widths and relations come from fixed allowlists. No arbitrary SQL, expressions or remote URLs.

| Tool | Purpose and distinguishing input | Output | Proposed limit / executed SQL reference |
|---|---|---|---|
| `describe_corpus` | Discover actual historical coverage and capabilities | Per-Dataset counts/bounds, parser/ownership availability and time basis | Two core Datasets; `probe_sql.py: describe_corpus` |
| `search_logs` | Retrieve examples; explicit all-term/any-term/phrase/fuzzy mode and filters | Stable `(dataset, source_file, entry_id)` keys, snippets, score, exact count | Default20/max100; term/phrase/fuzzy count+page probes |
| `get_context` | Anchor key and one real node/request/instance correlation; bounded before/after | Linked Log Entries and actual expanded Data Scope | Default30/max100; `get_context` probe. Reject absent keys rather than broadening scope |
| `count_by` | Calculate prevalence by up to three allowlisted dimensions | Exact matching Log Entry counts, total groups, unmapped/remainder counts | Default20/max100; `count_by` probe |
| `trend` | Fixed bucket and optional grouping | Counts per bucket, edge/coverage status, explicit missing-versus-zero semantics | Default200/max500 cells; `trend` probe |
| `top_patterns` | Rank a pinned template catalogue/version | Template/Component counts plus empty/unmatched/ambiguous diagnostics | Default20/max100; `load_patterns.py` GROUP BY and join probes |
| `latency_stats` | Select **HTTP request seconds OR instance build seconds** | Timed/untimed counts, p50/p95/p99/max, threshold counts and units | Default20/max100; both `latency_stats_*` probes. UUID first-to-last span is not request latency |
| `compare_periods` | Same filters, two explicit intervals | Before/after values, exposure durations/rates, absolute change; undefined baseline reason | `compare_periods` probe; no finite percentage change for zero baseline |
| `group_entries` | Explicit category/template/node/instance key and gap policy | Candidate membership bounds, counts/spread, policy and censor flags | Default20/max100; `group_entries` window query; no verified Incident claim |
| `get_evidence` | Investigation-scoped saved id | Immutable original queries/results and lineage | Saved pages; retrieval does not rerun SQL. Verification replay is a separate job producing a new comparison |

Whole-Base-Corpus aggregation is required: a universal seven-day maximum would break the challenge. Use per-query complexity/time limits, fixed relations and explicit scope. Proposed total response budget is 32KiB of final UTF-8 JSON, with field shortening flagged separately. Row caps alone cannot bound bytes/tokens. Limits are product proposals, not MCP requirements. Byte-bounded page handling, cursor signing, max-span grouping and zero-filled trend grids are **integration work**, not implemented by the research probes.

## Evidence and protocol boundaries

The settled glossary defines Truncated by comparing returned rows with matching Log Entries. That is ambiguous for a complete aggregation: millions of entries may intentionally produce ten groups. **Human clarification is required; no glossary was edited.** The draft retains that literal `truncated` comparison while adding `result_row_unit`, `total_result_rows`, `result_truncated`, `content_truncated`, exactness, queries/parameters, corpus version and investigation id. Prefer an approved glossary clarification that makes omitted result rows/content explicit. Never imply that exact aggregate counts were estimated from the displayed groups.

Record every executed statement (`queries[]`, purpose, parameters, duration), with top-level `query_ms` explicitly the sum of database timings, separate from end-to-end latency. Count/page must use the same immutable corpus/index version or a validated snapshot; an eventually refreshed index and separately mutated table can disagree. The experiment used a static complete import followed by `VACUUM (REFRESH_TABLE)`. It does not prove snapshot correctness under concurrent ingestion. Count timeouts are failures, never zero or invented estimates. Normalize psycopg Decimal values deliberately; counts beyond JavaScript's safe integer range need an exact string encoding and matching schema.

Current [MCP tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) supports output schemas and structured content; also include serialized JSON text for compatibility. Protocol list pagination does not paginate a search tool: our cursor must bind filters, stable tie-breakers, corpus version, identity/investigation and expiry. July 2026's explicit request metadata/state handles mean Evidence must not depend on an implicit transport session. Tool outputSchema does not constrain the host's final answer. Return schema-valid `isError:true` failures with recovery instructions. Full details and primary sources are in `tool-specialist.md`.

Each call appends user-visible Evidence. The conservative draft annotations are `readOnlyHint:false`, `destructiveHint:false`, `idempotentHint:false`; the database connection itself is read-only. If the team treats audit writes as incidental and chooses readOnlyHint true, document that interpretation. Annotations confer no permissions. `openWorldHint:false` describes the fixed local corpus; externally changing extensions need different treatment.

Resources: versioned `logs://schema`, `logs://corpus`, `logs://ownership`, `logs://templates/{dataset}`, and authorized Evidence pages. `describe_corpus` still supplies essential discovery because a resource's existence does not prove the model saw it. Three user-selected Role prompts require Observed Facts, Hypotheses, Unknowns, Next Step; Engineer selects linked raw context, Team Lead selects recurrence/ownership/trends, Manager selects measured frequency and affected Components with a decision. Issue #4 supplies the complete templates. Prompt arguments are validated strings, not arbitrary typed tool inputs. Prompts guide behavior; they do not authenticate Roles.

## Experiments and measured results

All SQL was checked against the SereneDB docs MCP before use. Local Docker is **SereneDB26.10.1**, image digest recorded in `research-environment.json`, linux/amd64 emulation, 3GiB container limit. Shared Apple Silicon machine activity was not isolated, so these are observed timings, not fair production benchmarks. `load_results.json` records exact input hashes and schema. The organiser's loader/schema/Ownership Table is unavailable; this is a throwaway research loader and must not replace the required supplied loader.

Executed and retained:

- Complete import and verification: **4,747,963 BGL +207,820 OpenStack =4,955,783 Log Entries**. Eight OpenStack physical continuations remain in whole-corpus counts. BGL's34,470 empty messages,316 irregular duplicated-location headers, four embedded-NUL messages and separate epoch/printed timestamps are preserved. `probe_nul.py` reproduces psycopg write_row rejection and demonstrates exact raw COPY round-trip, including backslash, tab/newline/CR, NULL and empty values.
- Full inverted index with frequency/position/offset/norm, refresh, BM25 and highlighting. Build observed **31.682s**, refresh0.382s. OpenStack `ts_phrase('build instance')`: **2,066** matches; BGL `ts_phrase('machine check')`: **40,026**; BGL token `error`: **557,845**; typo `erorr` at edit distance1 also557,845. Zero-match query returned zero and an empty page. Exact results and filters are in `sql-results.json`; no extrapolation from the five displayed examples.
- Parameter-bound injection-shaped Component string matched zero; the corpus remained intact. A dedicated SELECT-only reader counted all4,955,783 entries and searched the index; INSERT was denied with SQLSTATE42501. An aggregate-view-only Manager identity could query the view while raw-table SELECT was denied42501 (`security-results.json`). This validates the tested view/grant boundary, not universal Role security or RLS.
- Counts, daily trends, context, separate HTTP/build quantiles, period comparison and lag/window gap grouping all executed. The top one-node KERNDTLB group has152,182 entries; candidate-group semantics remain issue#1's proposal.
- Strict catalogue mapping of **498,143 distinct `(Dataset, Component, message)` keys** and raw-entry joins executed. BGL:4,630,794 uniquely assigned,161 ambiguous,34,470 empty,82,538 nonempty unmatched; OpenStack207,632 assigned,188 unmatched. Assignment totals reconcile exactly. Duplicate map keys=0; raw LEFT JOIN count remains4,955,783. `pattern-sql-results.json` retains top templates and daily assignment-status trends. The map is a research optimization; production should store versioned per-Log-Entry sidecar assignments after the supplied loader. Python classification coverage agrees with issue#6's independent full raw pass.

**Safety failure retained:** SereneDB accepted and SHOW confirmed `statement_timeout='100ms'`, but `pg_sleep(5)` completed in5.029s instead of raising57014. A follow-up with both reader and Manager identities also allowed a0.3s sleep. The upstream docs/test describe cancellation, but this installed image did not enforce it for this probe. Do not claim reliable hard cancellation. `probe_sql.py` intentionally exits at that failed assertion; `probe_security.py` records the remaining negative grants and reproduces the discrepancy. CPU-bound query timeout and driver cancellation are **unverified**; exclude arbitrary SQL and add a tested application deadline/cancel boundary before claiming resource protection. This narrow observation does not establish that all query timeouts fail.

## Comparison, sources and remaining decisions

[Anthropic tool-writing guidance](https://www.anthropic.com/engineering/writing-tools-for-agents) supports task-oriented interfaces, meaningful schemas, bounded results and held-out evaluation. [Advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use) reports improvements for large libraries with tool search; it does not prove ten tools beats six. Abstract-reviewed early papers [MCP-GRANITE](https://arxiv.org/abs/2609.24161) and [metadata interventions](https://arxiv.org/abs/2610.14334) suggest granularity/schema affect selection, but their settings are not our Claude log task and were not reproduced here.

Verified primary interface precedents: [Grafana](https://github.com/grafana/mcp-grafana) separates retrieval/statistics/patterns; [Datadog](https://docs.datadoghq.com/mcp_server/tools/) separates search and analysis; [ClickHouse](https://github.com/ClickHouse/mcp-clickhouse) combines discovery with read-only parameterized SQL; [Postgres MCP Pro](https://github.com/crystaldba/postgres-mcp) includes schema/EXPLAIN/index tools. Axiom and Elastic legacy OSS servers are deprecated in favor of hosted MCP/Agent Builder; don't present them as current equivalent OSS choices. General SQL wrappers offer flexibility we do not need, and their dialect/security does not transfer automatically to SereneDB. Detailed links are in the specialist audit.

SereneDB MCP sources: [inverted index](https://serenedb.com/docs/sql/statements/create_index/inverted), [full text](https://serenedb.com/docs/sql/functions/search/full-text#plainto_tsquery), [highlighting](https://serenedb.com/docs/cookbook/search/highlighting), [fuzzy](https://serenedb.com/docs/cookbook/search/fuzzy-search), [windows](https://serenedb.com/docs/sql/functions/window_functions), [timestamp](https://serenedb.com/docs/sql/functions/timestamp), [aggregates](https://serenedb.com/docs/sql/functions/aggregates), [GRANT](https://serenedb.com/docs/sql/statements/grant), [authentication](https://serenedb.com/docs/security/client_authentication), [configuration](https://serenedb.com/docs/configuration/overview).

Unresolved integration: supplied loader and Ownership Table; exact source-count/result-row glossary wording; actual Claude granularity A/B; supported Desktop/Code versions; pagination/serialization budgets; CPU timeout/cancellation. Team aggregation must fail explicitly until a unique official mapping is available, preserving an unmapped bucket thereafter. Optional index-create/drop tools need separate admin credentials, fixed targets and workload limits; remote Parquet/vector/OTel capabilities should be isolated extensions with measured scope, not arbitrary-URL escape hatches in the core server.

## Reproduce

Use a fresh disposable instance and the pinned Docker digest above (on ARM add `--platform linux/amd64`), publish only loopback port7890, and set a research password using the documented POSTGRES_PASSWORD bootstrap. Set DATABASE_URL to it. Fetch the checksum-pinned raw/annotation archives with issue#6's fetch_data.py. Create a uv environment with `psycopg[binary]==3.3.6`, `mcp==2.3.0`, `pydantic==2.14.1`. Then run:

```sh
python research/2/ugnmura/contracts.py
python research/2/ugnmura/probe_nul.py
python research/2/ugnmura/load_corpus.py --parser research/2/ugnmura/corpus_reader.py --data /tmp/serenedb-data
python research/2/ugnmura/probe_sql.py
# If the same documented timeout assertion fails, preserve that failure and run:
python research/2/ugnmura/probe_security.py
python research/2/ugnmura/load_patterns.py --data /tmp/serenedb-data
```

Do not rerun CREATE/import scripts against an existing imported instance; use a new disposable instance to avoid duplicate rows. Run issue#1's recorded SQL and issue#3's MCP compatibility checks for the companion findings.
