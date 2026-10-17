# Independent tool/protocol specialist audit — Codex

Research date: 2026-10-17. Read the issue body and the five required project documents. Did not read issue comments, post, push, or change settled project documents. This note owns no SereneDB SQL: the parent researcher verifies dialect and execution with the docs MCP and Docker. Findings below distinguish source facts from proposed design.

## Critical conclusions

1. **Current official SDK is `mcp==2.3.0`; its high-level class is `MCPServer`, formerly FastMCP.** Use `from mcp.server import MCPServer`. Standalone PrefectHQ `fastmcp` is **4.1.0**, not the issue's 2.x. These are different packages; do not mix their imports, decorators, contexts or migration guides. Official release dates: 2.3.0 on October 2; standalone 4.1.0 on October 8. Preserve the settled official-SDK choice. [SDK release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0), [official migration](https://py.sdk.modelcontextprotocol.io/migration/), [standalone release](https://github.com/PrefectHQ/fastmcp/releases/tag/v4.1.0).
2. **The Evidence contract currently confuses source Log Entries with result rows.** A complete aggregation of 1,000,000 entries into three Components returns three rows; it has not sampled 999,997 entries. Resolve this explicitly with the humans; do not quietly rewrite the glossary.
3. **MCP list pagination does not paginate search results.** `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list` have protocol cursors; `tools/call` needs our own cursor in input/output schemas. [Pagination specification](https://modelcontextprotocol.io/specification/2026-07-28/server/utilities/pagination).
4. **Ten tools are a testable design candidate, not an empirically established optimum.** Real server examples support discovery + query/analysis separation but do not establish selection accuracy for our Roles or Claude model.

## Current MCP facts that change implementation

The July 28, 2026 revision uses per-request metadata and explicit state handles. An investigation or ledger must not depend on an implicit MCP connection/session. Give requests an explicit opaque investigation handle or bind them to authenticated identity; authorize every access. The SDK handles protocol-era compatibility; test the actual Claude clients rather than constructing wire messages manually. Use stdio for the local judging path and Streamable HTTP as an additional deployment path using the same handlers. [Transport specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports).

Tool `inputSchema` roots are objects; JSON Schema 2020-12 is the default. Provide `outputSchema` and conforming `structuredContent`; include its serialized JSON as text for compatibility. There is no protocol-mandated 25,000-token result budget. Enforce our own size budget before final serialization; duplicated structured/text representations can increase the wire size. Return correctable execution failures with `isError:true` and specific recovery instructions, rather than raw driver tracebacks. [Tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools).

Prompts are intended for user selection, tools for model selection, resources for client-provided context. Resource availability does not guarantee the model sees a resource; retain `describe_corpus` as a discoverable bootstrap tool containing essential capabilities and compact scope facts. Prompt arguments are string-valued, rather than arbitrary typed tool JSON: accept simple `question`, `start`, `end`, `dataset` strings and validate them. Role prompt messages are user/assistant messages, not a new privileged system channel. Prompts do not enforce security. [Prompts specification](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts), [resources specification](https://modelcontextprotocol.io/specification/2026-07-28/server/resources), [schema: GetPromptRequestParams and ToolAnnotations](https://modelcontextprotocol.io/specification/2026-07-28/schema).

## Proposed Evidence envelope and the glossary conflict

Retain settled fields, but define units before implementation:

| Field | Proposed precise meaning |
|---|---|
| `total_match_count` | Exact count of source Log Entries satisfying the Data Scope, before pagination; nullable only for non-query operations with explicit `not_applicable` status. Never silently estimated. |
| `returned_count` | Number of displayed result rows, after row/byte limits. |
| `result_row_unit` | `log_entry`, `component_group`, `team_group`, `time_bucket`, `pattern_group`, `correlation_group`, etc. |
| `total_result_rows` | Total rows of this result shape before page limits; distinct from source entries. |
| `result_truncated` | Whether more result rows were omitted by a page or byte limit. |
| `content_truncated` | Whether any returned row's message/highlight/SQL text was shortened; include affected entry IDs or fields and recovery method. |
| `truncation_reasons` | Explicit enum list, e.g. `row_limit`, `byte_limit`, `field_limit`. |
| `queries[]` | Every executed SQL statement, parameters, duration, purpose; a single `sql` field cannot represent context+count queries faithfully. |
| `scope`, `corpus_version`, `ownership_version`, `template_version` | Meaning and reproducibility of the calculation; missing version/ownership must remain visible. |
| `evidence_id`, `parent_evidence_id`, `next_cursor` | Immutable call identity, page lineage, opaque application pagination. |

**Resolution proposal:** amend the glossary's Truncated definition to refer to omitted result rows/content, with units and source count explicit. Until approved, retain the literal existing `truncated` comparison and add `result_truncated` plus a conspicuous compatibility explanation. Otherwise a three-row complete aggregation will misleadingly look incomplete. Do not use legacy `truncated` alone in model-facing interpretation. The interim dual definition is awkward; approval of the clarified definition is preferable.

Count and page must describe the same data snapshot. Either compute them in a validated single statement, or use an immutable versioned corpus/index and record that version. A separate COUNT followed by a page query can disagree under mutation or eventually consistent index refresh. Do not claim transaction isolation semantics until verified in SereneDB. An exact count that times out is a failed calculation, not zero and not an estimate.

Pagination cursors should bind canonical filters, stable ordering with a unique tie-breaker, corpus/index version, user/investigation, and expiry. Sign or store opaque handles; reject filter/version changes. Keyset pagination avoids large OFFSET scans, but the exact SereneDB query must be tested. Full snapshot materialization may be excessive for 1B rows; do not store all matching logs merely to paginate a few examples.

**Retrieval is not replay:** `get_evidence` retrieves the saved immutable result and query; it must not rerun SQL and replace history. A verification replay is a separate CLI/job that produces a new comparison record and identifies corpus/version changes. If literally every call appends the ledger, retrieving E3 creates a new envelope referencing E3; never recursively embed the growing ledger.

## Review of the ten-tool candidate

These are design proposals, not measured model-selection results.

| Tool | Boundary worth preserving / issue to resolve |
|---|---|
| `describe_corpus` | Discovery, observed timestamp bounds, schema/capabilities, Ownership Table availability, count/time cost policy. Essential for historical corpora: defaulting to the last 24 hours would return nothing. Metadata calls cannot honestly invent executed SQL or source match counts. |
| `search_logs` | Bounded examples and ranked retrieval; never a frequency estimator. Put mode in an enum (`term`, `phrase`, `fuzzy`) with tested support; parameterize term values and compile fixed expressions. All filters usable by aggregation too. |
| `get_context` | Takes stable entry ID and explicit link key (`node`, `request`, `instance`) if available. Return actual link values and bounded before/after entries in deterministic timestamp+ID order. Context need not satisfy the original error predicate: explicitly state that scope expansion. |
| `count_by` | Exact source counts by a small allowlist of dimensions. Include unmapped Team/unknown Component buckets; avoid an inner Ownership Table join dropping entries or a non-unique mapping multiplying them. Source denominators and top-K omitted mass must be visible. |
| `trend` | Explicit bucket size/time zone/range and incomplete-edge-bucket indicators. Clarify whether missing buckets mean zero or missing source coverage. No client-side counting of retrieval pages. |
| `top_patterns` | Deterministic template counts, template version and unparsed coverage. It counts recurring message templates, not incidents. Do not call a group a causal problem automatically. |
| `latency_stats` | Capability-gated OpenStack measurement. Define start/end messages, pairing key, completion count, unmatched/duplicate pairs, elapsed unit and quantile method. UUID first-to-last span is not request latency by default. Reject unsupported measurements. |
| `compare_periods` | Two explicit ranges and the same filter/dimension semantics. Report counts plus exposure durations; unequal periods require normalized rates. Baseline zero implies undefined percent change, not infinity/100%. Return both underlying scopes/queries in one Evidence envelope. |
| `group_entries` | Highest overlap/ambiguity risk. Define deterministic correlation group keys and time-gap rules, version them, and call outputs candidate groups rather than Incidents. Consider keeping optional until the incident-model research settles. |
| `get_evidence` | Saved Evidence lookup only, by investigation-scoped opaque ID; pagination for large stored result. Does not authorize cross-investigation access or refresh measurements. |

A 6-tool core (describe/search/context/count/trend/evidence) covers required Role retrieval and analytics; patterns/latency/comparison add useful measured capabilities. The proposed ten-tool set should be compared against a consolidated analytics alternative under the same tasks. Do not hide all analytics behind a vague `analyze(action, options)` object without measuring parameter errors.

## Proposed limits and safety requirements

Provisional defaults to benchmark: search 20 entries/max100, context 10 before+10 after/max50 each, aggregations 20/max100 groups, trend max200 buckets, message excerpts max2KiB each, total serialized result max32KiB. These are proposed product limits, not MCP or Claude requirements. Count the final UTF-8 bytes of the complete result, preserving valid JSON/schema and all Evidence metadata. Row caps alone do not bound tokens. Byte caps bound transport size but do not guarantee tokenizer size; record tokens empirically for the judging client. If one row exceeds the budget, return a schema-valid shortened field with `content_truncated` and a recovery path.

Exact whole-Base-Corpus aggregation is required, so do not impose a universal seven-day maximum. Use validated query-specific time/complexity limits and separate optional huge-dataset policies. Explicit `all_corpus` or describe-derived bounds are preferable to invented recent dates. Distinguish database query duration from ledger/serialization/end-to-end duration. Bound pool/concurrency; cancellation should cancel the underlying database operation rather than only abandoning the HTTP response.

Bind values with psycopg parameters; identifier/table/dimension/order selection comes from a server-controlled allowlist (parameters cannot bind SQL identifiers). Never accept free SQL fragments, arbitrary relations, unrestricted expressions, remote URLs or raw ORDER BY. Parameterization prevents injection; it does not by itself prevent expensive fuzzy terms or pathological ranges. Set actual SereneDB-supported execution limits only after docs/local verification.

Use a dedicated read-only database identity for the core server. Index create/drop requires a separate optional admin server/credential with explicit index allowlists and workload quotas; it cannot honestly share a strict read-only identity. Never ship the postgres superuser as the normal tool connection.

Role prompts affect explanation, not authorization. If access control is offered, derive Role permissions from authenticated identity and enforce them on queries, resources, Evidence lookup, pagination and replay. A caller-supplied `role='manager'` is not security. Saved SQL/parameters/raw messages may leak restricted information even when the Manager summary is aggregate-only. Log text is untrusted data: instructions inside messages must not become tool authority.

Annotation choice requires an explicit environment boundary. The spec defines `readOnlyHint` as no modification, and idempotence as no additional environmental effect. With the settled append-on-every-call ledger, the conservative literal annotations are `readOnlyHint:false`, `destructiveHint:false`, `idempotentHint:false`; document that the only core write is additive Evidence, while the source database is read-only. If treating audit logging as incidental and using `readOnlyHint:true`, explain that interpretation rather than claiming strict no-side-effect semantics. `openWorldHint:false` can describe fixed versioned local corpora; use true for arbitrary/live external sources. These hints neither grant permissions nor make a call safe.

## Primary-source comparison and empirical limits

| Example | Verified exposed shape | Applicable lesson / limitation |
|---|---|---|
| [Grafana](https://github.com/grafana/mcp-grafana) | `query_loki_logs`, label metadata, `query_loki_stats`, `query_loki_patterns`; category enablement and RBAC. | Separate search, statistics and patterns; scope tool exposure. Its broad suite is not evidence our ten-tool selection is optimal. |
| [Datadog current tools](https://docs.datadoghq.com/mcp_server/tools/) | `search_datadog_logs` returns details; `analyze_datadog_logs` performs SQL statistics; product toolsets reduce context. | Clear retrieval/statistics distinction. Managed service, not a currently equivalent open-source server; old datadog-labs repo now directs users to hosted MCP. |
| [ClickHouse](https://github.com/ClickHouse/mcp-clickhouse) | `run_query`, `list_databases`, `list_tables`; default read-only; parameters; JSON strings for integers outside JS safe range. | Discovery and guarded execution; general SQL flexibility exceeds this challenge's need. Preserve exact large values: IDs as strings; counts beyond 2^53−1 need a documented string representation/schema. |
| [Postgres MCP Pro](https://github.com/crystaldba/postgres-mcp) | Schema discovery, `execute_sql`, EXPLAIN, health, index analysis; restricted/read-only mode. | Special-purpose performance tools can coexist with broad SQL; Postgres semantics do not establish SereneDB SQL/security support. |
| [Axiom current reference](https://axiom.co/docs/console/intelligence/mcp-server/tools) | Dataset/schema discovery, `queryDataset` APL, separate metric tools, prebuilt prompts. | Query-language distinction and discovery. [Old open-source server](https://github.com/axiomhq/mcp-server-axiom) is deprecated; do not present it as the current product. |
| [Elastic legacy repo](https://github.com/elastic/mcp-server-elasticsearch) | `list_indices`, `get_mappings`, `search`, `esql`, `get_shards`. | Deprecated in favor of Agent Builder MCP (9.2+); useful historical interface, not a current recommendation. |

[Anthropic's tool-writing article](https://www.anthropic.com/engineering/writing-tools-for-agents) recommends workflow-oriented tools, informative schemas/descriptions, bounded outputs, actionable errors and held-out evaluation. It explicitly says output-format effectiveness is task/model dependent. Its 25,000-token Claude Code default is a product default, not a target for our small log tool calls.

[Anthropic advanced-tool-use results](https://www.anthropic.com/engineering/advanced-tool-use) report internal large-library MCP accuracy changes with tool search: Opus4 49%→74%, Opus4.5 79.5%→88.1%, and roughly85% definition-token reduction. This changes loading/retrieval as well as context size; it does not isolate ten versus six tools or description length. Host tool discovery is not our MCP server's protocol feature.

Two current primary papers are relevant but not local proof. [MCP-GRANITE](https://arxiv.org/abs/2609.24161) reports 8,748 trials across nine local models (268M–20.9B), 81 IoT/edge scenarios and four granularity levels; a four-tool interface performed best there (reported completion improvements16.4% vs primitives and33.6% vs monolith). This is not evidence four tools wins for Claude log investigations. [Metadata interventions](https://arxiv.org/abs/2610.14334), submitted October9, report five models/five tasks: schemas affected selection strongly and top-level parameters were often favored. This is an early preprint and selection preference is not end-to-end task correctness. These statements were checked against the primary abstracts; full methods were not independently reproduced.

Required local evaluation proposal: fixed Role questions with acceptable tool/parameter sequences and golden SQL outcomes; compare core6, candidate10, consolidated analytics under identical model/version, prompts, ordering and budget. Score semantic filter validity, frequency-from-aggregation, factual answer correctness/citations, truncation honesty, recovery, calls/tokens/time. Test held-out paraphrases and ambiguous search-versus-count questions. Without actual Claude trials, report this as a protocol/schema/SQL prototype, never as measured tool-selection accuracy.

## Resources and prompt proposals

Resources: versioned `logs://schema`, `logs://corpus`, `logs://ownership`, `logs://templates/{dataset}`, `logs://evidence/{investigation}/{evidence}`; every URI is allowlisted and authorized. Resource templates need paging or bounded pages for large lists. Evidence resource links are useful but retain `get_evidence` for clients that do not automatically expose resources to the model.

Each Role prompt requires Observed Facts, Hypotheses, Unknowns, Next Step and cites `[E<n>]` beside measured claims. Engineer: find matching entries, inspect bounded linked context, distinguish plausible cause from established fact. Team Lead: aggregate full stated scope, map Components to Teams with unmapped coverage, trend recurring templates, then retrieve representative examples. Manager: prioritize only measured recurrence and affected Components, state denominators/scope, prohibit invented loss, downtime/customer impact and automated incident counts. A Role prompt must not force a causal conclusion when the data does not support one.

## Verification boundary

Verified release tags/dates and official import with GitHub API; fetched the current primary READMEs/docs. Web/primary-source review used agent-reach documented Jina/GitHub routes plus web search. `agent-reach check-update` could not run because the executable is absent; no installation performed. No GitHub comments inspected. No local Claude selection benchmark, no SDK install/run, and no SereneDB SQL execution performed by this specialist.
