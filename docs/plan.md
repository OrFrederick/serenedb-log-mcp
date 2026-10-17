# serenedb-log-mcp implementation plan (Phase 2)

> **For agentic workers:** this is a project plan made of tickets, not a step-by-step code plan. Each ticket becomes a GitHub issue labelled `ticket`. The implementer of a ticket works test-first with `superpowers:test-driven-development`, follows `overnight/PROMPT.md` section 6, and reads the Decision proposals linked in the ticket before writing code.

**Goal:** a read-only MCP server over SereneDB that lets Claude answer Engineer, Team Lead and Manager questions about the Base Corpus with cited Evidence, then extends to the award tracks (agent-managed index with benchmark, huge datasets, multimodal correlation, access control per Role).

**Architecture:** a post-load derive step turns the loader's raw tables into canonical tables, a Pattern dictionary and a default Incident table inside SereneDB. Parameterised tools resolve their tables through one resolver, run fixed SQL templates through one read-only database layer, wrap every result in an Evidence envelope, record it in the Evidence Ledger and shape it per Role. Claude checks its draft with `verify_answer`, and `export_investigation` turns a session into the worked investigation.

**Tech stack (settled):** Python, the official MCP SDK (`mcp` 2.3.0, where FastMCP is now `mcp.server.mcpserver.MCPServer`), psycopg 3, `uv`, pytest, ruff, mypy. SereneDB 26.10.1 on the Postgres wire protocol, port 7890.

Status: drafted overnight by NoNaim95's Claude (leader) on 2026-10-16/17 from the Decision proposals on issues #1 to #7, and revised after the fresh-context review on PR #10. All seven research issues have `consensus`: #1, #2, #4 and #5 by both Claudes, and #3, #6 and #7 by the leader alone while OrFrederick's Claude was absent. Nothing here is confirmed by the humans yet. Assumptions are listed in [Open points and assumptions](#6-open-points-and-assumptions).

---

## 1. Goal and how the plan maps to the brief

The brief (`docs/challenge-brief.md`) asks for a working prototype that turns logs into Role-specific answers, where SereneDB does the searching and calculating and Claude explains the Evidence. Our settled edges (`docs/decisions.md`) are: Log Entries are not Incidents, scoring against ground truth, self-verifying answers, the refusal moment, one investigation handed between Roles, and baselines on every number.

The minimum requirements are met **in capability** at the end of M1: the agent can answer a question per Role with retrieval and aggregation over both Datasets and show its work. They are **demonstrated**, and every deliverable exists, only at the end of M2, with the worked investigation (T18) and demo v1 with slides (T24). M3 measures the Best MCP Server items. Award-track tickets (M4) start only after that. The order of work is the [Priority order](#priority-order) in section 4.

| Milestone | Tickets | What exists afterwards |
|---|---|---|
| M0 Foundations | T1, T2, T3, T4 | Runnable server stub, Evidence contract, database layer, canonical tables from the dev loader, table resolver |
| M1 Minimum-requirement capabilities | T5, T6, T7, T8, T12, T15 | Search, context, counts and trends over both Datasets, Role layer, `verify_answer`, README and tool docs, contract tests and CI |
| M2 Headline and deliverables | T9, T10, T27, T11, T13, T14, T28, T18, T24 | Patterns, Incidents, OpenStack timing, `export_investigation`, Pattern tools and Manager ranking, the worked investigation, demo v1 and slides |
| M3 Best MCP Server measured, last differentiator | T17, T16 | Eval L1/L2 report, precursors and similar Incidents |
| M4 Award tracks | T19, T20, T21, T22, then T23, T26 | Agent-managed index with benchmark, huge datasets, multimodal correlation, access control per Role, MCP Apps visuals, optional vector search for OTel |
| Final | T29 | Demo, slides and README with every result that landed |
| Human-blocked | T25 | Swap in the organisers' loader and Ownership Table |

### Minimum requirements

| # | Requirement (brief) | Capability | Demonstrated in |
|---|---|---|---|
| 1 | At least one answer per Role with relevant Evidence, the right detail and a Role-fitting Next Step | T5, T6, T7 | T18 |
| 2 | Retrieval and aggregation, both BGL and OpenStack, at least one full-text answer, at least one whole-Base-Corpus aggregation, scope and filters stated | T4 (both Datasets), T5 (full-text `search_logs`), T6 (`count_by(group_by='dataset')` over the whole Base Corpus), Data Scope in every envelope (T2, T3) | T18 (search Evidence plus one aggregation over both Datasets) |
| 3 | Practical recommendations backed by findings | T7 (Next Step per Role), T12 (`verify_answer`) | T18, T16 (precursors as labelled Hypotheses) |
| 4 | Tools exposed with clear descriptions | T3 (annotations), T8 (`docs/tools.md`), T15 (contract tests on descriptions) | T17 (tool choice measured) |
| 5 | Answers show their work, with hypotheses and limitations labelled | T3 (Evidence Ledger), T7 (four answer sections), T12, T13 | T18 |
| 6 | Runs from a clean checkout with setup instructions | T1, T4 (`scripts/fetch_data.py`, `make derive`), T8 (README), T15 (CI from scratch) | T8 (fresh-clone dry run), T29 |

### Deliverables

| Deliverable | Ticket |
|---|---|
| Public repository with README (workflow, setup, run, limitations) | T8, final pass in T29 |
| Working prototype | M0 to M2 (T1 to T15, T18, T24, T27, T28) |
| Documentation for each tool (description, inputs, outputs) | T8 (`docs/tools.md`, generated from the live registry) |
| One worked investigation for all three Roles, with search Evidence and an aggregation | T18 (exported by `export_investigation`) |
| Demo of 5 minutes or less, plus slides | T24 (v1 on the core), T29 (final); the recorded backup video is human-only |

### Scoring

| Criterion | Weight | Where the plan earns it |
|---|---|---|
| Role-appropriate insight | 30% | Role layer (T7), Incidents with Spread and Shape (T10, T27), Manager ranking band (T28), worked investigation (T18) |
| Search + analytics together | 25% | `search_logs` facets (T5), `count_by`/`trend` with baselines (T6), Pattern-first search chain (T14), Incidents (T10, T27) |
| Recommendation quality | 25% | Next Step per Role (T7), `verify_answer` (T12), precursors with support and lift (T16), ground-truth scores (T10, T11) |
| Execution, novelty, presentation | 20% | Eval harness (T15, T17), demo (T24, T29), agent-managed index (T19), huge datasets (T20), multimodal (T21), visuals (T23) |

### Award order
Core plus **Best MCP Server** first (M0 to M3; the Best MCP Server criteria map to T3, T7, T8, T15 and T17: clear descriptions, correct tool choice measured by eval, sensible limits, and connection instructions that work). Then, in the settled order: **agent-managed index with benchmark** (T19), **huge datasets** (T20), **multimodal correlation** (T21), **access control per Role** (T22).

---

## 2. Architecture

### 2.1 Data flow

```
Organisers' loader (TODO(organiser), T25)      Dev loader (scripts/dev_loader.py, T1)
                      \                          /
                 raw tables in schema public (bgl, openstack, lh2_bgl, lh2_openstack)
                                   |
             sql/adapters/<adapter>.sql   <- the only loader-specific code (T4, T25)
                                   |
             scripts/derive.py runs sql/derive/*.sql in name order, in schema SLM_SCHEMA
                                   |
     +-----------------+------------------+------------------+---------------------+
     | canonical       | Pattern          | default Incident | OpenStack instance  |
     | entries (T4)    | dictionary (T9)  | table (T10, T27) | phases (T11)        |
     | + Ownership     | slm.pattern_dict | slm.incident     | slm.os_instance     |
     |   stub (T4)     | slm.pattern      | gap 300 s, all   |                     |
     +-----------------+------------------+------------------+---------------------+
                                   |   inverted indexes + VACUUM (REFRESH_TABLE)
                                   v
   tools/*.py -> corpus.datasets.relation() -> fixed SQL templates -> db.py (READ ONLY, cancel timeout, retry once)
                                   |
                     Evidence envelope (evidence.py)
                                   |
                     Evidence Ledger: E1, E2, ... (ledger.py, JSONL at LEDGER_PATH, shared across sessions)
                                   |
                     Role hook: answer_frame, not_measured, Manager shaping (roles/)
                                   |
                     structuredContent  ->  Claude drafts an answer with [E<n>]
                                   |
                     verify_answer(role, draft): checks every number and citation
                                   |
                     export_investigation -> Markdown in the result; scripts write the file;
                     scripts/replay_ledger.py re-runs the ledger
```

The derive step is re-runnable and idempotent. It never depends on a stable line number from the loader, because the Pattern dictionary joins on message text (#6).

### 2.2 Canonical schema (the adapter seam)

Every tool queries these names only, and only through the resolver `corpus.datasets.relation(dataset, role, scope)` (T4). The adapter SQL maps whatever the loader produced to them, so replacing the dev loader with the organisers' loader touches `sql/adapters/official.sql` and nothing else (T25). Derived tables live in the schema named by `SLM_SCHEMA` (default `slm`; SereneDB supports `CREATE SCHEMA`), which also lets parallel implementers on the shared dev instance use their own schema, for example `SLM_SCHEMA=slm_t10`.

| Table | Columns (names are binding for tools; types are the implementer's call) | Built by |
|---|---|---|
| `bgl_entry` | `entry_id`, `ts` (as logged, US Pacific local time), `epoch_utc` (UTC seconds, used for every gap, window, bucket and baseline, section 2.6), `node`, `rack`, `midplane`, `nodecard`, `component`, `level`, `alert_tag` (NULL for non-alert lines), `message`, `template_id`, `family_id`, `pattern_key` | T4; Pattern columns filled by T9 |
| `os_entry` | `entry_id`, `ts`, `epoch_utc`, `source_id` (neutral id per source file, never the file name), `log_file`, `pid`, `component`, `level`, `request_id`, `instance_id`, `instance_ref` (UUID from `[instance: …]` or `/servers/<uuid>`), `endpoint`, `http_status`, `latency_s`, `message`, `template_id`, `family_id`, `pattern_key` | T4; Pattern columns filled by T9 |
| `pattern_dict` | `dataset`, `message`, `template_id`, `template_text`, `template_source` (`loghub2`, `sqlmask`, `empty`), `event_id`, `family_id` (the Pattern dictionary of #6: one row per distinct message) | T9 |
| `pattern` | `dataset`, `template_id`, `template_text`, `template_source`, `event_id`, `family_id`, `component`, `level`, `entries`, `first_seen`, `last_seen` (one row per Pattern; the catalogue behind `list_patterns`) | T9 |
| `incident` | `dataset`, `incident_id`, `pattern_key`, `t0`, `t1`, `entries`, `bursts`, `nodes`, `nodecards`, `midplanes`, `racks`, `top_node`, `top_node_share`, `shape` | T10 (BGL), T27 (OpenStack Request Failures) |
| `os_instance` | `instance_id`, phase durations (spawn, build, …), robust z-scores | T11 |
| `ownership` | `dataset`, `component`, `team` (stub, `TODO(organiser)`), `team_source` (where the Team comes from: `stub` until T25, #48) | T4, replaced in T25 |

"Pattern" is the concept and `template_id` is the column (#6 Decision proposal). `pattern_key` is the key Incidents are built on (#1). T4 sets it to `alert_tag` for BGL alert lines and to NULL for non-alert lines (`alert_tag = '-'`) and for OpenStack. T9 then fills `template_id` and `family_id`, and sets `pattern_key = template_id` wherever it is still NULL. Until T9 lands, Incidents exist only for BGL alert categories, which is enough for T10.

The OpenStack ground-truth labels never reach a tool. The dev loader's `source_file` contains `openstack_abnormal.log`, so the adapter maps file names to neutral `source_id` values. The labels are read only by `scripts/ground_truth.py`, and tests assert that no canonical column and no tool output contains the word "abnormal" (T4, T15).

### 2.3 MCP surface

The server is registered as `serenedb-log`, so Claude Code shows tools as `mcp__serenedb-log__<tool>`. No tool-name prefix (assumption, see open points).

**Tools.** Every tool takes a required `role` enum (`engineer`, `team_lead`, `manager`, #4), returns its result in `structuredContent`, and sets `_meta["anthropic/alwaysLoad"]=True` and a `title`. Every tool except `create_index` and `drop_index` sets `readOnlyHint`, `idempotentHint`, `openWorldHint=false` and `destructiveHint=false`.

| # | Tool | One-line purpose | Ticket |
|---|---|---|---|
| 1 | `describe_corpus` | What data exists: Datasets, time ranges, corpus end date, Components with Teams, levels, Pattern counts. Call it first. | T5 |
| 2 | `search_logs` | Find specific Log Entries by full-text query (all words, phrase, any, fuzzy) or by Pattern ids, with time and Component filters; returns an exact total, facets and at most 50 rows. Never for "how often". | T5 |
| 3 | `get_context` | Log Entries just before and after one entry on the same node, request or instance, or in a time window. | T5 |
| 4 | `count_by` | Exact counts of matching Log Entries grouped by one dimension (Dataset, Component, Team, level, node, rack, midplane, alert category, Pattern), with an optional baseline period. | T6 |
| 5 | `trend` | Zero-filled counts per time bucket, optionally per group (at most 5 series, 400 buckets), with baselines. | T6 |
| 6 | `list_patterns` | The Pattern catalogue of a Dataset (optionally one Component), so Claude can read it and pick the Pattern ids for a concept. Use it first. | T14 |
| 6a | `search_patterns` | Lexical fallback: BM25 with stemming over template text plus a committed synonym map, for when the catalogue is too long to read. | T14 |
| 7 | `top_patterns` | Patterns ranked by Log Entries, Incidents, sources, recency or Manager priority within a Data Scope, with Pareto share, Chronic and New Pattern flags and drift hand-offs. | T28 |
| 8 | `list_incidents` | Incidents (#1): Log Entries of one Pattern chained by `gap_s`, with Spread, Shape and `top_node_share`, side by side with entry counts; `similar_to` finds earlier look-alike Incidents (#7). | T27, T16 |
| 9 | `latency_stats` | OpenStack API latency percentiles per endpoint and bucket, spawn and build durations per instance, Latency Degradation and Phase-Duration Anomalies. | T11 |
| 10 | `find_precursors` | Patterns that precede a target Pattern within a window more often than a baseline, with support and lift; always a Hypothesis. | T16 |
| 11 | `get_evidence` | Re-fetch a stored Evidence record by id, from this or an earlier session, shaped for the Role. | T3 |
| 12 | `get_role_brief` | The Role playbook as a tool, for clients that do not show MCP prompts. | T7 |
| 13 | `verify_answer` | Check a draft answer: every `[E<n>]` resolves, every number is in its cited Evidence, sections present, Role rules hold. | T12 |
| 14 | `export_investigation` | Return the session's questions, tool calls, SQL, results and answers as Markdown in the result, shaped for the audience Role. Read-only; scripts write the file. | T13 |
| 15 | `create_index` | Stretch, not read-only: build an inverted index over a time slice and report the build time. | T19 |
| 16 | `drop_index` | Stretch, not read-only: remove an agent-created index. | T19 |
| 17 | `get_trace` | Stretch: the spans, Log Entries and metrics of one OTel trace. | T21 |
| 18 | `find_patterns` | Stretch, OTel only: semantic Pattern search with `fastembed` vectors stored in SereneDB, fused with BM25 and synonyms. | T26 |

Fifteen core tools (rows 1 to 14 and 6a) stay below the tool counts where selection is reported to degrade (#2). The overlap risk among `list_patterns`, `search_patterns` and `top_patterns` is measured by the L1 eval (T17); see open points.

**Catalogue-first chain (#7).** Claude reads `list_patterns`, picks Pattern ids, and passes them on: `count_by(pattern_ids=[…])`, `search_logs(pattern_ids=[…])` (where `query` is then optional) and `list_incidents(pattern_key=…)`. `pattern_ids[]` and `family_id` are shared Data Scope filters that every Evidence tool accepts (scope handling in T2; integration test in T14).

**Limits** (#2 Decision proposal): `search_logs` rows default 20, maximum 50; `count_by` groups default 25, maximum 100; `get_context` default 10, maximum 50 entries per side; `trend` at most 400 buckets; `list_incidents` default 20, maximum 100; `top_patterns` default 20, maximum 50; `latency_stats` maximum 50 groups. Messages are capped at 240 characters per row. Responses stay under Claude Code's 10k-token warning and 50k-character spill threshold.

**Evidence envelope** (T3, every Evidence tool): `evidence_id`, `tool`, `role`, `sql[]`, `params[]`, `data_scope{datasets, time_from, time_to, time_input, resolved_against, filters}` (filters include `pattern_ids[]` and `family_id`), `total_matches`, `returned`, `truncated`, `query_ms`, `columns[]`, `rows[][]`, `summary{}` (tool-specific scalars such as `share_pct`, `top_n_sum`, `other_count`, `groups_total`, `entries_total`, `incidents_total`), `notes[]` (steering text, hints, truncation and retry notes), `answer_frame[]` and `not_measured[]` (Role layer). The text content mirrors the same JSON for clients other than Claude Code. Errors are raised as `ToolError` with an actionable message, because on errors only `content[0].text` reaches the model and in `mcp` 2.x only `ToolError` text survives.

**Prompts:** `engineer`, `team_lead`, `manager`, each with single-token arguments because Claude Code splits prompt arguments on whitespace (T7).

**Server instructions** (at most 4,096 characters, T7): the Datasets; retrieval returns samples and aggregation answers frequency; cite `[E<n>]` next to every number and quote exact numbers; Log Entries are not Incidents; answer in four sections (Observed Facts, Hypotheses, Unknowns, Next Step); call `verify_answer` before answering; the Role prompts and `get_role_brief` exist.

**Resources:** none planned. The #2 research proposed depth-only resources; every fact they would carry is already reachable through a tool, and Claude Code does not read resources automatically.

### 2.4 Database access layer (T2)

- **Read-only:** every statement runs in a read-only transaction (psycopg `conn.read_only = True`, which issues `BEGIN READ ONLY`). `SET default_transaction_read_only` is not enforced by SereneDB, so it is not used.
- **Timeouts:** `statement_timeout` is accepted but not enforced. A `threading.Timer` calls `conn.cancel_safe()` after `SLM_QUERY_TIMEOUT_S` (default 15 s); the connection stays usable afterwards (measured in #2).
- **Retry and reconnect (#2 round 2):** SereneDB 26.10.1 crashes with SIGSEGV intermittently, the crash can hit an innocent later call, and binding mode makes no difference. On a lost connection (`OperationalError`) the layer waits until `select 1` succeeds on a new connection (a restart took about 2 to 3 s), retries the statement once and adds a note to the result. If the retry fails too, it raises a `ToolError` that says the database restarted and suggests `sort=time`.
- **Binding:** parameters are always bound, never formatted into SQL. psycopg's default server-side binding is used; `SLM_CLIENT_BINDING=1` switches to `psycopg.ClientCursor`. Injection strings were inert in both modes (#2).
- **Relevance sort (#2 round 2):** `sort=relevance` uses `ORDER BY BM25(idx.tableoid) DESC LIMIT n` with no tie-break key after the score. A tie-break key after the score crashed native 26.10.1 in 28 of 70 runs; the single-key form crashed in 0 of 44. `sort=time` is the default. Because ties are unordered, replays compare `total_matches` for relevance results, not row identity.
- **Limits and truncation:** the exact total comes from a separate `count(*)` (faster than `count(*) OVER ()`); the row query is skipped when the total is 0; `truncated = returned < total_matches`.
- **SQL rules:** fixed templates only; table and index names come from the resolver, other identifiers from enums, never from input; user text goes through `websearch_to_tsquery`; in relevance mode, filters use `col @@ ts_between(...)::score(NULL)` so they do not change BM25 scores, and every range bound carries an explicit cast (`ts_ge(%s::TIMESTAMP)` on `ts`, `ts_ge(%s::DOUBLE)` on `epoch_utc`), so a statement stored in the Evidence Ledger, whose parameters are JSON, replays as stored (#71); point lookups (`get_context`) run on the base table, never through the inverted index. On 26.09.1.3 with an index over a view, a `line_no` predicate made the index ignore the `ts` filter; it did not reproduce on 26.10.1 with a base-table index, but base-table lookups are cheap and correct either way (#2).
- **Per-Role connections:** the layer uses `DATABASE_URL_<ROLE>` when it is set and `DATABASE_URL` otherwise (implemented in T2; the roles and views come in T22).

### 2.5 Configuration

| Variable | Default | Used by |
|---|---|---|
| `DATABASE_URL` | `host=localhost port=7890 dbname=postgres user=postgres` | everything |
| `DATABASE_URL_ENGINEER`, `DATABASE_URL_TEAM_LEAD`, `DATABASE_URL_MANAGER` | unset (fall back to `DATABASE_URL`) | db layer (T2), roles and views (T22) |
| `SLM_SCHEMA` | `slm` | derive step and resolver |
| `SLM_DATA_DIR` | `./data` (gitignored) | fetch script, dev loader, pattern generator |
| `SLM_QUERY_TIMEOUT_S` | `15` | db layer |
| `SLM_CLIENT_BINDING` | `0` (psycopg default binding) | db layer |
| `LEDGER_PATH` | unset (in-memory only) | Evidence Ledger, shared across server processes and sessions; eval harness |
| `SLM_PATTERN_TIER1` | `1` | Pattern dictionary (T9); CI sets `0` |
| CLI `--transport stdio|http`, `--port`, `--role <role>` | stdio, no Role pin | server entry point |

### 2.6 Environment facts the implementation relies on

- SereneDB 26.10.1 (dev) speaks the Postgres wire protocol on port 7890. The dev instance runs on a remote box behind an SSH tunnel and is shared by all implementers. The documented route for judges is a local `docker run serenedb/serenedb` with `-e SERENEDB_HOST_AUTH_METHOD=trust`.
- `statement_timeout` and `default_transaction_read_only` are not enforced; use `cancel_safe()` and `BEGIN READ ONLY`.
- The server SIGSEGVs intermittently. The known trigger is a BM25 sort with a tie-break key after the score (28 of 70 runs on native x86_64 26.10.1); parameter binding is not a factor, and a crash can surface on a later query. The database layer must reconnect and retry, and no SQL template may sort by BM25 plus a second key.
- `CREATE TEXT SEARCH DICTIONARY … AS split_text(...)` exists only from 26.10.1. OrFrederick's Claude verified the legacy `(TEMPLATE='segmentation')` form on 26.09.1.3. The derive step picks the form by server version.
- `time_bucket` drops empty buckets; gap-fill with `generate_series(timestamp, timestamp, interval)`.
- **Time zones (#65).** BGL's `ts` is US Pacific local time with daylight saving time; OpenStack's `ts` carries no zone and is UTC. Measured on the full Base Corpus, `epoch_utc - ts` is exactly 25,200 s (PDT, 3,669,612 rows) or 28,800 s (PST, 1,078,351 rows) for BGL, switching at 2005-10-30 09:00 UTC, when local time went from 02:00 back to 01:00; OpenStack has no row where they differ. So the local hour from 01:00 on 2005-10-30 holds two hours of Log Entries (49 BGL entries: 11 in 08:00 UTC, 38 in 09:00 UTC). This one gap is also why the ungrouped 300 s chain count is 8,149 on `epoch_utc` against 8,148 on `ts` (`tests/corpus/test_canonical_corpus.py`). The registry says which zone `ts` is in (`Dataset.ts_zone`, `ts_is_local`; #48).
- **Time arithmetic runs on `epoch_utc` only, never on a local `ts`.** Gaps, windows, buckets, baseline periods and every other time comparison use UTC: `epoch_utc` for a Dataset whose `ts` is local time, `ts` itself for a Dataset that logs UTC. `clock.py` holds the rule (`utc_time` is the SQL expression, `make_timestamp(CAST(round(epoch_utc * 1000000) AS BIGINT))` for BGL; SereneDB has no `to_timestamp`). `count_by`, `trend` and `search_logs` (#73) read time inputs as UTC, report every computed time in UTC, and say so in the Data Scope (`time_zone`, `time_columns`); `search_logs` filters BGL's time range on `epoch_utc` in the inverted index (`epoch_utc @@ ts_ge(%s::DOUBLE)::score(NULL)`, measured score-neutral), and `describe_corpus` states `first_entry`, `last_entry` and `corpus_end` in UTC. Rows of Log Entries keep `ts` as logged, with a note that gives the offset to UTC; `get_context` still states its window in logged time (`time_zone` null).
- A bare `@@ 'a b'` is OR; `websearch_to_tsquery` gives AND and quoted phrases.
- The inverted index is eventually consistent; run `VACUUM (REFRESH_TABLE) <table>` after loading and after `CREATE INDEX`.
- An inverted index over a view needs a fast-path source to return real columns. A view over a list of files is a generic view: it can be indexed and answers counts and scores, but selecting its real columns raises an error ("Indexing Views" page of the SereneDB docs).
- A SELECT-only login role can still read server files (`read_csv('/etc/passwd')`) and change some server-wide settings, such as `enable_external_access`. Never run experiments that `SET` server-wide settings on the shared instance.
- `mcp` 2.3.0: `from mcp.server.mcpserver import MCPServer`, `from mcp.server.mcpserver.exceptions import ToolError` (as used in `research/2-NoNaim95:research/2/NoNaim95/prototype/server.py`).
- Claude Code 2.1.293 (NoNaim95's probe; OrFrederick's #2 probe ran on 2.1.263 with the same result) shows the model only `structuredContent` on success and only `content[0].text` on errors. Without `_meta["anthropic/alwaysLoad"]` the tools sit behind ToolSearch. `outputSchema` is never shown to the model, so each description must summarise its output fields.
- The dev loader (`/Users/deni/hackathon/tools/dev_loader.py`, moved into the repo in T1) loads `bgl(line_no, alert_tag, epoch, ts, node, component, level, message, raw)`, `openstack(line_no, source_file, log_file, ts, pid, level, component, request_id, instance_id, message, raw)`, `lh2_bgl` and `lh2_openstack`. It is throwaway and is not the organisers' loader. The organisers' loader, schema and Ownership Table have not arrived.
- KERNDTLB on the dev data (checked on the shared instance): the category has 152,734 Log Entries on 541 nodes; node R30-M0-N9-C:J16-U01 has 152,182 of them, all on 2005-06-11 local time (17:17 to 23:39 PDT), which is 2005-06-12 00:17 to 06:39 UTC, the day `trend` reports.

### 2.7 Module contracts

These interfaces let the two owners build in parallel. Each is owned by one ticket, and the other side builds against the merged version, not a draft.

| Interface | Owner | Contract |
|---|---|---|
| `evidence.py`: `Role`, `DataScope`, `Evidence` | T3 (O) | The Role enum, the Data Scope (Datasets, time range, `time_input`, `resolved_against`, `filters` including `pattern_ids[]` and `family_id`) and the envelope fields of section 2.3. |
| `db.py`: `QueryResult` | T2 (N) | Every query helper returns `QueryResult(total, rows, truncated, query_ms, sql, params, notes)`. `notes` carries retry and timeout notes. The database layer never builds an `Evidence`. |
| `scope.py` | T2 (N) | Builds T3's `DataScope` from tool inputs: enum validation with hints, corpus-relative time, shared filters. |
| `corpus.datasets.relation(dataset, role, scope)` | T4 (N) | Returns the qualified table and index names and the column map for a Dataset, Role and Data Scope. The Dataset entry also names the IANA time zone of `ts` (`ts_zone`, `ts_is_local`; #48). Every tool must use it; a contract test fails if a tool module contains a table or index name. T19 (agent indexes), T20 (remote Datasets) and T22 (per-Role views) change only the resolver. |
| `toolkit.evidence_tool` | T3 (O) | Wraps a tool function that returns `QueryResult`, `DataScope`, `columns` and `summary`, builds the `Evidence`, writes the ledger, applies the Role hook and the `--role` pin, and maps exceptions to `ToolError`. |
| `ledger.py` | T3 (O) | Append-only JSONL at `LEDGER_PATH`, loaded at startup, numbering continued across sessions, appends under a file lock. `get_evidence`, `verify_answer`, `export_investigation` and `scripts/replay_ledger.py` read it. |
| `verify/checks.py` | T12 (O) | Pure answer checks shared by `verify_answer` and the eval's L2 grader. |

---

## 3. Module layout

```
pyproject.toml                    uv project; deps; ruff, mypy and pytest config; console script serenedb-log-mcp
uv.lock                           pinned dependencies
Makefile                          T1's targets (help, db-up, load-dev, test, test-db, test-corpus, lint, typecheck) + `-include mk/*.mk`
mk/data.mk                        fetch-data, derive (N, T4 onwards)
mk/quality.mk                     test-golden, eval (O, T15 and T17)
mk/docs.mk                        docs (O, T8)
mk/bench.mk                       bench-index, bench-huge (N, T19 and T20)
.mcp.json                         serenedb-log (stdio, uv run) next to serenedb-docs
.github/workflows/ci.yml          lint, typecheck, unit, contract and db tests against a serenedb/serenedb service container
src/serenedb_log_mcp/
  __init__.py
  __main__.py                     python -m serenedb_log_mcp -> cli.main
  cli.py                          --transport stdio|http, --port, --role pin
  config.py                       settings from the environment (section 2.5)
  server.py                       create_server(): MCPServer with instructions, prompts and auto-discovered tools
  instructions.py                 server instructions text, at most 4,096 characters (T7)
  db.py                           read-only transactions, cancel timeout, reconnect and retry once, per-Role DSNs, QueryResult (T2)
  scope.py                        builds DataScope: enum validation with hints, corpus-relative time, shared filters (T2)
  evidence.py                     Role enum, DataScope and Evidence envelope models (T3)
  ledger.py                       Evidence Ledger: E1.., JSONL at LEDGER_PATH, shared across sessions, file lock (T3)
  toolkit.py                      evidence_tool decorator: annotations, alwaysLoad, ToolError mapping, ledger, Role hook, --role pin (T3)
  corpus/
    datasets.py                   Dataset registry and the resolver relation(dataset, role, scope) (T4)
    ownership.py                  Ownership Table lookups (T4)
    synonyms.yaml                 curated synonym map for search_patterns (T14)
  roles/
    __init__.py                   Role briefs, answer frames, not_measured lists, Manager shaping (T7)
    prompts.py                    MCP prompts engineer, team_lead, manager (T7)
  verify/
    checks.py                     answer checks shared by verify_answer and eval L2 (T12)
  export/
    markdown.py                   investigation report renderer (T13)
  tools/
    __init__.py                   auto-discovery: every module exposes register(server)
    describe_corpus.py            T5
    search_logs.py                T5
    get_context.py                T5
    count_by.py                   T6
    trend.py                      T6
    list_patterns.py              T14
    search_patterns.py            T14
    top_patterns.py               T28
    list_incidents.py             T27 (T16 adds similar_to)
    latency_stats.py              T11
    find_precursors.py            T16
    get_evidence.py               T3
    get_role_brief.py             T7
    verify_answer.py              T12
    export_investigation.py       T13
    agent_index.py                create_index, drop_index (T19)
    get_trace.py                  T21
    find_patterns.py              T26 (stretch, OTel only)
sql/
  adapters/dev.sql                canonical tables from the dev loader's raw tables, neutral OpenStack source ids (T4)
  adapters/official.sql           TODO(organiser) stub listing the required columns (T4), filled in T25
  derive/NN_*.sql                 ordered derive steps: entries, patterns, incidents, OpenStack phases, indexes (T4, T9, T10, T11, T27)
  seed/ownership_stub.sql         stub Ownership Table, TODO(organiser) (T4)
  access/roles.sql                per-Role views, LOGIN roles, GRANTs (T22)
scripts/
  dev_loader.py                   throwaway dev loader, not the organisers' loader (T1)
  fetch_data.py                   downloads the Base Corpus (Zenodo 8196385) and Loghub-2.0 (Zenodo 8275861) (T4)
  derive.py                       runs the adapter and derive SQL, idempotent (T4)
  gen_pattern_sql.py              generates the Tier 1 CASE chain from Loghub-2.0 templates at derive time (T9)
  score_patterns.py               scores Pattern assignment against Loghub-2.0 EventIds (T9)
  ground_truth.py                 the only reader of labels: Incidents vs Oliner & Stearley, OpenStack 4/4 (T10, T11)
  replay_ledger.py                re-runs ledger SQL and checks numbers in final answers (T13)
  export_report.py                writes export_investigation Markdown for a ledger and answers file to disk (T13)
  gen_tool_docs.py                writes docs/tools.md from the live tool registry (T8)
  run_worked_investigation.py     drives Claude through the #5 investigation and writes the report (T18)
  demo_replay.py                  offline replay of a recorded ledger for the demo (T24)
eval/
  driver.py                       claude -p (and optional Agent SDK) runner (T17)
  graders/trajectory.py           L1 checks (T17)
  graders/grounding.py            L2 checks, reusing verify/checks.py (T17)
  questions.yaml                  about 40 questions with golden values (T17)
  run.py, report.py               k trials, pass rate, pass^k, Wilson CIs (T17)
bench/
  index_tradeoff.py               agent-managed index benchmark (T19)
  huge_scale.py                   remote Parquet scale benchmark (T20)
tests/
  conftest.py                     markers db and corpus, auto-skip with a reason (T1)
  unit/                           pure tests per module
  db/                             tests that need SereneDB with at least the sample data
  contract/                       L0 contract tests (T15), resolver contract test (T4)
  golden/                         independent golden SQL against tool results (helper T5); run with make test-golden
docs/
  plan.md                         this file
  tools.md                        generated (T8)
  worked-investigation.md         generated (T18)
  results/                        patterns.md, ground-truth.md, eval.md, index-tradeoff.md, huge-datasets.md, multimodal.md
  demo/                           script.md, slides.md (T24, T29)
```

**Type checker: mypy** (strict on `src/`, with the `pydantic.mypy` plugin). It installs as a pure-Python wheel through `uv sync`, so a clean checkout and CI need no Node.js. The `pyright` PyPI package downloads Node on first run, which adds a moving part to the clean-checkout path. psycopg 3 and pydantic ship type information, so strict mode is realistic.

**Makefile: split into per-area includes.** T1's `Makefile` stays as T1 writes it. T4, the first later ticket that adds targets, appends `-include mk/*.mk` to it and makes sure `make help` reads `$(MAKEFILE_LIST)`, so included targets are listed. From then on, each ticket adds targets only to its area's `mk/<area>.mk` (owners in the layout above), and nobody else edits the root `Makefile`.

**Test markers:** unmarked tests are pure and run everywhere. `db` tests need a reachable SereneDB at `DATABASE_URL` with at least the Loghub 2k samples loaded and derived. `corpus` tests need the full Base Corpus; golden numbers live there and run with `make test-golden`. Missing prerequisites skip with a clear reason, never fail.

**Generated files:** `docs/tools.md` is regenerated with `make docs`. When two PRs conflict on it, re-run `make docs` instead of hand-merging.

---

## 4. Tickets

Each ticket below becomes one GitHub issue with the body format from `overnight/PROMPT.md` section 5.4. Owners: **N** is NoNaim95's Claude, **O** is OrFrederick's Claude. Every ticket's gate is: `make lint`, `make typecheck` and `make test` pass, plus `make test-db` for tickets that touch SQL, with the command output summarised in the PR. Prototype paths refer to `origin/research/<n>-<user>` branches; read them with `git show origin/research/<n>-<user>:<path>`. T-numbers are stable identifiers, not the order of work: T1 already exists as issue #11, and tickets added during the review are numbered T27 onwards.

Decision proposal links used below:
- #1 Incident model: [6226295740](https://github.com/OrFrederick/serenedb-log-mcp/issues/1#issuecomment-6226295740)
- #2 MCP tool set: [6225924233](https://github.com/OrFrederick/serenedb-log-mcp/issues/2#issuecomment-6225924233) (consensus of both Claudes), with [6225923811](https://github.com/OrFrederick/serenedb-log-mcp/issues/2#issuecomment-6225923811), [6225390739](https://github.com/OrFrederick/serenedb-log-mcp/issues/2#issuecomment-6225390739) and [6225524070](https://github.com/OrFrederick/serenedb-log-mcp/issues/2#issuecomment-6225524070), plus NoNaim95's round-2 amendments [6226499531](https://github.com/OrFrederick/serenedb-log-mcp/issues/2#issuecomment-6226499531) (single-key relevance sort, reconnect and retry once, `ClientCursor` optional; NoNaim95's alone, not yet confirmed by OrFrederick)
- #3 Eval harness: [6225712371](https://github.com/OrFrederick/serenedb-log-mcp/issues/3#issuecomment-6225712371) (consensus by the leader alone: [6226634528](https://github.com/OrFrederick/serenedb-log-mcp/issues/3#issuecomment-6226634528))
- #4 Role differentiation: [6225763701](https://github.com/OrFrederick/serenedb-log-mcp/issues/4#issuecomment-6225763701) and [6225875106](https://github.com/OrFrederick/serenedb-log-mcp/issues/4#issuecomment-6225875106)
- #5 Worked investigation: [6225730757](https://github.com/OrFrederick/serenedb-log-mcp/issues/5#issuecomment-6225730757) and [6225870253](https://github.com/OrFrederick/serenedb-log-mcp/issues/5#issuecomment-6225870253)
- #6 Template mining (leader alone): [6226634275](https://github.com/OrFrederick/serenedb-log-mcp/issues/6#issuecomment-6226634275), based on NoNaim95's [Research 6226346837](https://github.com/OrFrederick/serenedb-log-mcp/issues/6#issuecomment-6226346837)
- #7 Embeddings (leader alone): [6226634068](https://github.com/OrFrederick/serenedb-log-mcp/issues/7#issuecomment-6226634068), based on NoNaim95's [Deliberation 6225923222](https://github.com/OrFrederick/serenedb-log-mcp/issues/7#issuecomment-6225923222)

### Summary

| T# | Title | Owner | Blocked by | Milestone | Priority |
|---|---|---|---|---|---|
| T1 | Project skeleton | N | – | M0 | 1 |
| T2 | Database access layer and Data Scope | N | T1, T3 | M0 | 3 |
| T3 | Evidence contract: envelope, Evidence Ledger, tool wrapper, `get_evidence` | O | T1 | M0 | 2 |
| T4 | Corpus adapter, resolver, derive step, indexes and stub Ownership Table | N | T1 | M0 | 4 |
| T5 | Retrieval slice: `describe_corpus`, `search_logs`, `get_context` | O | T2, T3, T4 | M1 | 5 |
| T6 | Aggregation slice: `count_by` and `trend` with baselines | N | T2, T3, T4 | M1 | 6 |
| T7 | Role layer: Role shaping, prompts, instructions, `get_role_brief` | O | T3, T5 | M1 | 9 |
| T8 | README v1 and generated tool documentation | O | T5, T6, T7 | M1 | 10 |
| T9 | Pattern dictionary (#6) | N | T4 | M2 | 7 |
| T10 | Default Incident table and ground-truth check (#1) | N | T4 | M2 | 8 |
| T11 | `latency_stats` and OpenStack anomaly classes | N | T2, T3, T4 | M2 | 14 |
| T12 | `verify_answer` and shared answer checks | O | T3, T7 | M1 | 11 |
| T13 | `export_investigation` and ledger replay | O | T3, T12 | M2 | 15 |
| T14 | Pattern catalogue: `list_patterns`, `search_patterns` and the catalogue-first chain | N | T5, T6, T9, T27 | M2 | 16 |
| T15 | Eval L0: contract tests, golden SQL runner and CI | O | T5, T6, T12 | M1 | 12 |
| T16 | `find_precursors` and similar Incidents | N | T9, T10, T27 | M3 | 21 |
| T17 | Eval L1/L2: driver, graders, question set, report | O | T7, T11, T12, T14, T15, T27, T28 | M3 | 20 |
| T18 | Worked investigation via `export_investigation`, pinned by golden SQL | O | T5, T6, T11, T13, T27, T28 | M2 | 18 |
| T19 | Agent-managed index: `create_index`, `drop_index` and benchmark | N | T5, T6 | M4 | 22 |
| T20 | Huge datasets: `text_bench` in place, scale report | N | T19 | M4 | 23 |
| T21 | Multimodal correlation: `otel_demo` logs, traces, metrics, `get_trace` | N | T20 | M4 | 24 |
| T22 | Access control per Role: views, LOGIN roles | O | T5, T6, T7 | M4 | 25 |
| T23 | MCP Apps visuals (human check needed) | O | T27 | M4 | 26 |
| T24 | Demo v1 and slides on the core | O | T12, T18 | M2 | 19 |
| T25 | Swap in the organisers' loader and Ownership Table (**human-blocked**) | N | T4 + organiser materials | – | when materials arrive |
| T26 | Stretch: `find_patterns` with `fastembed` vectors for the OTel track | N | T14, T20 | M4 | 27 |
| T27 | `list_incidents` tool and OpenStack Request Failure Incidents | N | T2, T3, T9, T10 | M2 | 13 |
| T28 | `top_patterns` with Manager priority ranking, Chronic/New flags and drift | N | T6, T9, T10, T27 | M2 | 17 |
| T29 | Final pass: demo, slides and README with all results | O | T17, T24 | Final | 28 |

### Priority order

Start the highest-priority ticket that is unblocked. The order puts the minimum requirements and the deliverables (including the worked investigation and demo v1 with slides) before any award-track ticket, and it respects every dependency. T9 and T10 sit early because the worked investigation waits on them through T27 and T28.

1. T1 Project skeleton
2. T3 Evidence contract
3. T2 Database access layer and Data Scope
4. T4 Corpus adapter, resolver, derive step
5. T5 Retrieval slice
6. T6 Aggregation slice
7. T9 Pattern dictionary
8. T10 Default Incident table and ground-truth check
9. T7 Role layer
10. T8 README v1 and tool docs
11. T12 `verify_answer`
12. T15 Eval L0 and CI
13. T27 `list_incidents` and OpenStack Request Failures
14. T11 `latency_stats` and OpenStack anomalies
15. T13 `export_investigation` and ledger replay
16. T14 Pattern catalogue and catalogue-first chain
17. T28 `top_patterns` and Manager ranking
18. T18 Worked investigation
19. T24 Demo v1 and slides on the core
20. T17 Eval L1/L2
21. T16 `find_precursors` and similar Incidents
22. T19 Agent-managed index (award track 1)
23. T20 Huge datasets (award track 2)
24. T21 Multimodal correlation (award track 3)
25. T22 Access control per Role (award track 4)
26. T23 MCP Apps visuals
27. T26 `find_patterns` for OTel (stretch)
28. T29 Final pass
- T25 runs whenever the organisers' materials arrive.

Critical path to the deliverables: T1 → T4 → T9 → T27 → T28 → T18 → T24, alongside T1 → T3 → T2 → T5 → T7 → T12 → T13.

Rules for using the order: each Claude works down the list within its own tickets. When one Claude is absent (overnight protocol, section 3), the other takes its unstarted tickets and follows the same list, so a single Claude still finishes the minimum requirements and deliverables first. A Claude whose own core tickets are all started or done may begin award-track tickets; it does not take the other Claude's core tickets unless that Claude is absent.

---

### T1: Project skeleton
- **Owner:** N. **Area:** build and infrastructure. **Blocked by:** none. **Links:** #2 (SDK and transport), `docs/decisions.md` (stack).
- **Goal:** create the uv project and the smallest runnable MCP server so every later ticket builds on the same commands, markers and layout. The server starts over stdio with `MCPServer`, discovers tool modules automatically, and has no real tools yet. The throwaway dev loader moves into the repo so anyone can load the Base Corpus into a local SereneDB.
- **Acceptance criteria:**
  - [ ] From a clean clone, `uv sync` succeeds and `uv run serenedb-log-mcp --help` lists `--transport stdio|http`, `--port` and `--role`. A test starts the server over stdio and gets valid `initialize` and `tools/list` responses.
  - [ ] `make lint` (ruff check and ruff format check), `make typecheck` (mypy strict on `src/` with the pydantic plugin) and `make test` pass; `make help` lists every target.
  - [ ] Any module in `src/serenedb_log_mcp/tools/` that exposes `register(server)` is registered automatically; a test with a temporary tool module proves it.
  - [ ] `scripts/dev_loader.py` is in the repo, with a header that says it is not the organisers' loader. It reads `SLM_DATA_DIR` and `DATABASE_URL`, and has a `--sample` mode that loads the Loghub 2k BGL and OpenStack samples (for CI). `make db-up` starts `serenedb/serenedb` on port 7890 with `SERENEDB_HOST_AUTH_METHOD=trust`, and `make load-dev` loads the data.
  - [ ] `tests/conftest.py` defines the `db` and `corpus` markers; their tests skip with a clear reason when SereneDB or the full Base Corpus is missing. `make test-db` and `make test-corpus` run them.
  - [ ] `.mcp.json` gains a `serenedb-log` stdio entry (`uv run serenedb-log-mcp`) next to `serenedb-docs`, and `config.py` reads the variables in plan section 2.5.
- **Reuse:** `/Users/deni/hackathon/tools/dev_loader.py`; the server bootstrap in `research/2-NoNaim95:research/2/NoNaim95/prototype/server.py`.

### T2: Database access layer and Data Scope
- **Owner:** N. **Area:** data access. **Blocked by:** T1, T3. **Links:** #2 Decision proposal, NoNaim95's Research (safety table) and round-2 amendments, plan sections 2.4 and 2.7.
- **Goal:** one module through which every tool runs SQL, so that read-only enforcement, timeouts, crash recovery and truncation are written once and tested once, plus the Data Scope builder that fills T3's `DataScope`.
- **Acceptance criteria:**
  - [ ] Every statement runs in a read-only transaction. A `db` test shows that INSERT and CREATE through the layer fail, while a full-text query, a `GROUP BY` and a `time_bucket` query succeed.
  - [ ] A `threading.Timer` calls `conn.cancel_safe()` after `SLM_QUERY_TIMEOUT_S`. A `db` test with a deliberately slow query and a 0.5 s timeout raises the layer's timeout error within about 2 s, and the next query succeeds.
  - [ ] On a lost connection the layer waits until `select 1` succeeds on a new connection (bounded wait), retries the statement once and adds a note to the result; if the retry fails, it raises a `ToolError` that says the database restarted and suggests `sort=time`. Unit-tested with a fake connection that fails once and one that always fails.
  - [ ] `fetch_limited(count_sql, rows_sql, params, limit)` returns `QueryResult(total, rows, truncated, query_ms, sql, params, notes)` and skips the row query when the total is 0. Parameters are always bound (psycopg's default); `SLM_CLIENT_BINDING=1` switches to `psycopg.ClientCursor`, and an injection string passed as a value matches 0 rows in both modes.
  - [ ] `scope.py` builds T3's `DataScope`: it validates enum inputs with "did you mean" hints (`KERNAL` suggests `KERNEL`), resolves relative times such as `last 7d` against the Dataset's last timestamp (recording both the input and the resolved range), and accepts the shared filters `pattern_ids[]` (16-hex `template_id` values) and `family_id`.
  - [ ] `SLM_SCHEMA` is validated against `^[a-z_][a-z0-9_]*$`, and the connection for a Role uses `DATABASE_URL_<ROLE>` when it is set and `DATABASE_URL` otherwise.
- **Reuse:** `_q` in `research/2-NoNaim95:…/prototype/server.py`; `research/2-NoNaim95:…/survey/exp_readonly_tx.py`; `research/2-OrFrederick:…/deliberation/throwaway_db/c4_readonly.py` and `c5_timeout.py`; `research/2-NoNaim95:…/prototype/crash_discriminate.py` (crash shapes).

### T3: Evidence contract: envelope, Evidence Ledger, tool wrapper, `get_evidence`
- **Owner:** O. **Area:** MCP surface (shared contract). **Blocked by:** T1. **Links:** #2 Decision proposal, #3 Decision proposal (ledger per trial), #4 Decision proposal (`role` on every tool, `answer_frame`, `not_measured`), plan sections 2.3 and 2.7.
- **Goal:** define the contracts every tool builds against: the Role enum, the Data Scope, the Evidence envelope, the Evidence Ledger and a decorator that turns a plain query function into a well-behaved MCP tool. Keep it small and merge it fast, because T2, T5, T6, T7, T11 and T27 build on it.
- **Acceptance criteria:**
  - [ ] `evidence.py` defines `Role`, `DataScope` (with `filters` that include `pattern_ids[]` and `family_id`) and `Evidence` with the fields in plan section 2.3. A validator enforces `truncated == (returned < total_matches)`, and field descriptions document each field.
  - [ ] `ledger.py` assigns `E1, E2, …` in call order (a regression test covers the ordering bug found in the #3 pilot). When `LEDGER_PATH` is set, it loads the existing file at startup, continues the numbering, and appends each record under an advisory file lock, re-reading the last id under the lock; `get(id)` and `all()` read the file, so Evidence from earlier sessions stays addressable.
  - [ ] A test runs two server processes on one `LEDGER_PATH`, calls tools in both, and checks that the ids are unique and gap-free and that the second process can `get_evidence` an id the first one created.
  - [ ] `toolkit.evidence_tool` requires `role` on every tool, registers a title, the read-only annotations and `_meta["anthropic/alwaysLoad"]=True`, returns the envelope as `structuredContent` with the same JSON in one text block, writes the ledger, calls a Role hook (identity by default, replaced in T7), rejects a `role` other than the `--role` pin with a `ToolError`, and maps every exception to a `ToolError` with an actionable message.
  - [ ] `get_evidence(role, evidence_id)` returns the stored record through the Role hook, so the Manager gets no raw rows once T7 lands; an unknown id raises a `ToolError` that names the valid range.
  - [ ] A contract test with an in-process client calls a sample tool and checks that `structuredContent` validates against the output schema, that the text mirrors it, and that the error path yields `isError` with the actionable text.
- **Reuse:** the models in `research/2-NoNaim95:…/prototype/server.py`; the ledger and error wrapper in `research/3-NoNaim95:research/3/NoNaim95/harness/stub_server.py`.

### T4: Corpus adapter, resolver, derive step, indexes and stub Ownership Table
- **Owner:** N. **Area:** data. **Blocked by:** T1. **Links:** #1 (UTC epoch, topology, `pattern_key`), #2 (precomputed columns, index), #6 (join on message text), plan sections 2.2 and 2.7.
- **Goal:** build the adapter seam and the table resolver. A re-runnable derive step maps the dev loader's raw tables to the canonical tables, adds the derived columns every tool needs, keeps the OpenStack labels out, builds the inverted indexes and seeds a stub Ownership Table, so swapping in the official loader later is one file and tools never name a table themselves.
- **Acceptance criteria:**
  - [ ] `scripts/fetch_data.py` downloads BGL and OpenStack (Zenodo 8196385) and the Loghub-2.0 BGL and OpenStack files (Zenodo 8275861) into `SLM_DATA_DIR`, verifies their sizes, and is idempotent. The root `Makefile` gains `-include mk/*.mk`, `make help` lists included targets, and `mk/data.mk` holds `fetch-data` and `derive`.
  - [ ] `make derive` runs `sql/adapters/dev.sql` and then `sql/derive/*.sql` in name order inside `SLM_SCHEMA`. A second run gives identical row counts. A `corpus` test asserts 4,747,963 rows in `bgl_entry` and 207,820 in `os_entry`.
  - [ ] BGL `rack`, `midplane` and `nodecard` are parsed from `Rxx-My-Nz-…` node names (NULL for other names, and the count of those is reported); `epoch_utc` is computed so that the #1 DST check holds; OpenStack gets `endpoint`, `http_status`, `latency_s` and `instance_ref`. Both tables carry `template_id` and `family_id` (NULL until T9) and `pattern_key`, set to `alert_tag` for BGL alert lines and NULL for `'-'` and for OpenStack.
  - [ ] The adapter maps OpenStack file names to neutral `source_id` values (for example `os-1`, `os-2`, `os-3` in load order). A `db` test checks that no text column of any canonical table contains "abnormal", and the anomaly labels are not loaded into any canonical table.
  - [ ] Inverted indexes cover `message` (dictionary with frequency, position and offset) and the verbatim filter columns, followed by `VACUUM (REFRESH_TABLE)`. The dictionary DDL is chosen by `SELECT version()`: the `AS split_text(...)` form on 26.10.1 or later, the legacy template form before that.
  - [ ] `corpus/datasets.py` provides the resolver `relation(dataset, role, scope)` (plan section 2.7), and a contract test fails if any module under `tools/` contains a canonical table or index name. `sql/seed/ownership_stub.sql` maps every Component of both Datasets to a fictional Team with a `TODO(organiser)` marker, and `sql/adapters/official.sql` exists as a `TODO(organiser)` stub that lists the columns it must produce.
- **Reuse:** `research/2-NoNaim95:…/prototype/precompute.py`, `bench.py` (endpoint and latency regexes) and `index_build.py`; `research/1-OrFrederick:…/deliberation/v_dst.sql` (UTC and DST); `research/1-NoNaim95:…/experiments/01_keys.sql` (topology keys).
- **Human-only input:** the organisers' schema and Ownership Table (see T25). The dev adapter and the stub unblock everything else.

### T5: Retrieval slice: `describe_corpus`, `search_logs`, `get_context`
- **Owner:** O. **Area:** tools (Engineer path). **Blocked by:** T2, T3, T4. **Links:** #2 Decision proposal and round-2 amendments, NoNaim95's Research table (SQL that worked), OrFrederick's Deliberation (score filters), #7 (catalogue-first chain).
- **Goal:** the first end-to-end tracer bullet. Claude can learn what data exists, find Log Entries by full-text search or by Pattern ids with time and Component filters, and see what happened just before and after one entry, all with Evidence.
- **Acceptance criteria:**
  - [ ] `describe_corpus(role, dataset?)` returns, per Dataset, the rows, the time range, the corpus end date, the Components with counts and Teams, the levels and the Pattern count when present, with a note that Log Entries are not Incidents. The result is cached after the first call.
  - [ ] `search_logs(role, query?, pattern_ids?, family_id?, mode, dataset, time_from, time_to, component[], level[], source, sort, limit)` requires `query` or a Pattern filter, uses `websearch_to_tsquery` for `query`, sorts by time by default, gets the exact total from a separate count, caps messages at 240 characters with highlights, and returns facets (distinct sources and racks, first and last seen, top Components and levels).
  - [ ] In relevance mode, filters use `::score(NULL)` and the sort is `ORDER BY BM25(idx.tableoid) DESC LIMIT n` with no tie-break key after the score (#2 round 2); a unit test asserts that no SQL template sorts by BM25 plus another key. Zero matches returns a hint, not an error.
  - [ ] `get_context(role, dataset, entry_id, scope, before, after, window_s)` returns ordered rows with an anchor flag. It queries the base table, never the inverted index. The instance scope matches `instance_id = x OR message LIKE '%x%'`.
  - [ ] `tests/golden/` gets a helper that compares a tool result with independent SQL; `corpus` golden tests cover three fixed queries. All three tools take table and index names from `corpus.datasets.relation`.
  - [ ] Each description states purpose, inputs and outputs in 3 to 6 sentences and says when to use another tool instead. `search_logs` says that it does not answer "how often".
- **Reuse:** `research/2-NoNaim95:…/prototype/server.py`, `filter_score.py`, `search_variants.py`; `research/2-OrFrederick:…/facets_probe.py`, `deliberation/lineno_filter_bug.py`.
- **Why O owns it:** N's queue holds the critical path (T4, T9, T10, T27), and this ticket exercises the envelope O defines in T3.

### T6: Aggregation slice: `count_by` and `trend` with baselines
- **Owner:** N. **Area:** tools (Team Lead and Manager path). **Blocked by:** T2, T3, T4. **Links:** #2 Decision proposal, #3 follow-up (`share_pct`, `top_n_sum`, `other_count`, `groups_total`), `docs/decisions.md` Differentiator 7 (baselines).
- **Goal:** exact counts grouped by one dimension and zero-filled trends over any Data Scope, including the whole Base Corpus, with a baseline next to every number so the agent can say whether a count is unusual.
- **Acceptance criteria:**
  - [ ] `count_by(role, group_by, query?, filters, top, baseline_from?, baseline_to?)` supports `dataset`, `component`, `team`, `level`, `node`, `rack`, `midplane`, `alert_category` and `pattern`, and accepts the shared filters including `pattern_ids[]` and `family_id`. Each group has `count`, `share_pct`, `distinct_sources`, `first_seen` and `last_seen`. The summary has `total`, `groups_total`, `top_n_sum` and `other_count`, and an `(other)` row keeps the totals exact.
  - [ ] `count_by(group_by='dataset')` with no filters covers the whole Base Corpus; a golden test matches independent SQL (4,747,963 and 207,820 on the dev data).
  - [ ] `trend(role, bucket, query?, group_by?, filters)` zero-fills with `time_bucket` and `generate_series`, allows at most 5 series, and raises a `ToolError` that explains how to recover when more than 400 buckets would be needed.
  - [ ] When `trend` is grouped by Pattern, a series whose Pattern has a drift partner (the same `family_id`, from T9 once it lands) carries a hand-off note, so a wording change is not read as a Pattern ending (#1). Before T9 lands, the note is absent and the description says so.
  - [ ] Every count and series carries the previous-period value, the median-week value and a normal or unusual flag; the method is stated in `notes`.
  - [ ] `group_by='team'` joins the Ownership Table, and unmapped Components appear as `(unmapped)`.
  - [ ] Descriptions say "quote exact numbers, never round", and results note that the counts are Log Entries, not Incidents.
- **Reuse:** `count_by` and `trend` in `research/2-NoNaim95:…/prototype/server.py`; `research/2-NoNaim95:…/prototype/bench.py`.

### T7: Role layer: Role shaping, prompts, instructions, `get_role_brief`
- **Owner:** O. **Area:** answer side. **Blocked by:** T3, T5. **Links:** #4 Decision proposal and round 2, plan section 2.3.
- **Goal:** make the three Roles differ in what the server returns, not only in tone. The Role hook adds the answer frame and the Manager's not-measured list, withholds raw lines from the Manager, and the prompts and instructions tell Claude how each Role answers.
- **Acceptance criteria:**
  - [ ] The Role hook adds `answer_frame` (the four sections and the Role's kind of Next Step) to every Evidence result, and `not_measured` (cost, downtime, customer impact, SLA and root cause, as in OrFrederick's #4 Research) for the Manager, inside `structuredContent`.
  - [ ] For `role='manager'`, raw-line rows are withheld from `search_logs`, `get_context` and `get_evidence` of raw-line Evidence; the result keeps totals and facets, and a note points to `count_by`, `top_patterns` and `list_incidents`.
  - [ ] MCP prompts `engineer`, `team_lead` and `manager` take single-token arguments and carry the #4 playbooks. `get_role_brief(role)` returns the same text as a tool.
  - [ ] `instructions.py` stays at or below 4,096 characters (a test enforces it) and covers the points in plan section 2.3.
  - [ ] A contract test shows that the same question through the three Roles yields different results (for example, the Manager's `search_logs` returns no rows), so the Roles differ in substance.
- **Reuse:** `research/4-NoNaim95:research/4/NoNaim95/experiments/r4e_evidence.py` and `r4e_evidence_ledger.md`; `research/3-NoNaim95:…/deliberation/prompt_server.py` (prompts as slash commands); `research/4-OrFrederick:research/4/OrFrederick/COMMENT.md` (Role templates).

### T8: README v1 and generated tool documentation
- **Owner:** O. **Area:** documentation. **Blocked by:** T5, T6, T7. **Links:** brief (Deliverables, minimum requirement 6), `docs/decisions.md` ("play to the sponsor").
- **Goal:** a judge can go from a clean checkout to Claude answering a question by following the README alone, and every tool has generated documentation.
- **Acceptance criteria:**
  - [ ] The README covers what the project is, the workflow, setup (`docker run serenedb/serenedb` with `SERENEDB_HOST_AUTH_METHOD=trust`, `uv sync`, `make fetch-data`, loading with the supplied loader as a `TODO(organiser)` section plus the dev loader fallback, `make derive`), connecting Claude Code (`.mcp.json` or `claude mcp add`) and Claude Desktop (a JSON snippet), running, limitations, and the citations (Zhu et al., ISSRE 2023; Oliner & Stearley, DSN 2007; Jiang et al., ISSTA 2024 for Loghub-2.0).
  - [ ] `scripts/gen_tool_docs.py` writes `docs/tools.md` from the live registry (name, title, description, inputs, output fields, limits). `make docs` (in `mk/docs.mk`) runs it, and a test fails when `docs/tools.md` is stale.
  - [ ] A "SereneDB features used" section lists the inverted index, BM25, `websearch_to_tsquery`, `ts_between`, `time_bucket`, window functions, macros and remote Parquet, with doc links.
  - [ ] A "Known SereneDB caveats" section covers the unenforced `statement_timeout` and `default_transaction_read_only`, and the intermittent SIGSEGV (the two-key BM25 sort) with retry.
  - [ ] A fresh-context subagent follows only the README on a clean clone and reaches a working `tools/list` in Claude Code; the PR includes a summary of its run.
- **Note:** only O edits `README.md`. Other tickets write results to `docs/results/*.md`, which the README links.

### T9: Pattern dictionary (#6)
- **Owner:** N. **Area:** data. **Blocked by:** T4. **Links:** #6 Decision proposal (leader alone) and NoNaim95's Research, #1 (Pattern as Incident key, template drift).
- **Goal:** give every Log Entry a Pattern inside SereneDB, as one derive step over distinct messages: Tier 1 is a generated SQL `CASE` chain from the Loghub-2.0 templates, Tier 2 is the generic g10 mask macro for everything Tier 1 cannot match.
- **Acceptance criteria:**
  - [ ] `scripts/gen_pattern_sql.py` generates the Tier 1 chain at derive time from the downloaded Loghub-2.0 files, strict `\S+` patterns before loose `.*` ones, most specific first. Neither the template files nor the generated SQL are committed. Tier 1 is on by default and `SLM_PATTERN_TIER1=0` turns it off; if Tier 1 is on and the files are missing, the derive step stops with a message that names `make fetch-data` and `SLM_PATTERN_TIER1=0`.
  - [ ] Tier 2 is the g10 `CREATE MACRO` chain (not the label-tuned v10). NULL messages get `template_source='empty'`.
  - [ ] `pattern_dict` (one row per distinct message) and `pattern` (one row per Pattern) are built as in plan section 2.2, with `template_id` the first 16 hex characters of `md5` of the template key (a content hash, never a parser cluster id), and `family_id` the `md5` of the template text without a trailing ` over <*> seconds`. Log Entries join on message text; the canonical tables' `template_id` and `family_id` are filled, and `pattern_key` becomes `template_id` wherever it was NULL.
  - [ ] Merge gate from the #6 Decision proposal: `scripts/score_patterns.py` scores the regenerated chain in SQL against the Loghub-2.0 labels, and agreement on covered lines must be at least 99.99% on BGL and OpenStack; the target is 99.9999%, as measured in #6. The PR lists every disagreeing template. Coverage per `template_source` and the derive time go to `docs/results/patterns.md`.
  - [ ] A test shows that the six "over N seconds" template pairs from #6 (E218/E112, E63/E34, E70/E40, E78/E43, E119/E67, E191/E92) each share one `family_id`.
- **Reuse:** `research/6-NoNaim95:research/6/NoNaim95/in_db/exp2_lh2_regex.py` (CASE chain generator), `in_db/mask_macros.sql`, `in_db/mask_ga.py`, `in_db/exp3.sql`, `parsers/template_match.py` (strict-then-loose reference), `parsers/metrics.py`.

### T10: Default Incident table and ground-truth check (#1)
- **Owner:** N. **Area:** data. **Blocked by:** T4. **Links:** #1 consolidated Decision proposal.
- **Goal:** the headline, as a derive step. Turn Log Entries into Incidents with Spread and Shape, precompute the default table, and prove the definition against the published ground truth. The `list_incidents` tool and the OpenStack Request Failure Incidents follow in T27.
- **Acceptance criteria:**
  - [ ] A derive step builds `incident` over every row with a non-NULL `pattern_key`, with `gap_s=300` and `merge_scope=all`, computing gaps on `epoch_utc` and the Incident id as a running `sum()` cast to BIGINT. Each Incident has the Spread vector (nodes, node cards, midplanes, racks), `top_node_share` and a Shape label.
  - [ ] `scripts/ground_truth.py` reproduces 1,202 Incidents for the BGL alert categories (Oliner & Stearley) and writes `docs/results/ground-truth.md`, including the derive time; a `corpus` test asserts 1,202.
  - [ ] Golden test with this exact wording: "The largest KERNDTLB Incident has 152,182 Log Entries on 1 node (2005-06-11). The category has 152,734 Log Entries."
  - [ ] Re-running `make derive` after T9 adds the template-keyed BGL Incidents without code changes, because the step reads `pattern_key` only; a `db` test on the samples checks that the rebuild is idempotent.
- **Reuse:** `research/1-NoNaim95:research/1/NoNaim95/experiments/02_bursts.sql`, `03_incidents.sql`, `05a_classify_spread_shape.sql`, `literature/logfilter_T300.sql`; `research/1-OrFrederick:research/1/OrFrederick/sql_incidents.sql`, `sql_shape.sql`, `sql_spatial.sql`.

### T11: `latency_stats` and OpenStack anomaly classes
- **Owner:** N. **Area:** data and tools. **Blocked by:** T2, T3, T4. **Links:** #1 (Latency Degradation, Phase-Duration Anomaly), #2 (`latency_stats`), #5 (4 of 4 slow spawns).
- **Goal:** answer OpenStack timing questions that text search cannot: latency percentiles per endpoint, Latency Degradation over time, and spawn or phase durations that are anomalous, scored against the labelled anomalies.
- **Acceptance criteria:**
  - [ ] `latency_stats(role, metric, endpoint?, bucket?, filters)` with `metric` in `api`, `spawn`, `build` returns `n`, p50, p95, p99 and max per group from precomputed columns (`quantile_cont`), at most 50 groups.
  - [ ] Latency Degradation: per-bucket p95 and p99 with a baseline and a rise flag.
  - [ ] A derive step builds `os_instance` with phase durations and robust z-scores (median and MAD). The 4 labelled slow spawns rank as anomalies (4 of 4); the result is added to `docs/results/ground-truth.md`; the unlabelled d7d68893 appears and is flagged as a Hypothesis.
  - [ ] The ground-truth labels are read only by `scripts/ground_truth.py`; no tool or derive step reads them.
  - [ ] Golden tests pin the 4 instance ids and their spawn times.
- **Reuse:** `research/1-OrFrederick:…/sql_os_latency.sql`, `sql_os_lifecycle.sql`, `sql_os_detect.sql`, `sql_os_inst.sql`; `research/5-NoNaim95:research/5/NoNaim95/r5_golden.py` (the 4 UUIDs).

### T12: `verify_answer` and shared answer checks
- **Owner:** O. **Area:** answer side. **Blocked by:** T3, T7. **Links:** `docs/decisions.md` Differentiator 3, #4 (Role checks), #3 (L2 grounding rules and pilot failures), #1 (Log Entries, Incidents and Spread side by side).
- **Goal:** the agent checks its own draft before answering. One pure module holds the checks, so `verify_answer` and the eval's L2 grader can never disagree.
- **Acceptance criteria:**
  - [ ] `verify/checks.py` checks that every `[E<n>]` resolves in the ledger, that every number in a cited sentence appears in the cited Evidence (rows, summary, totals) or is marked "(derived from [E1],[E2])" and recomputes correctly, and that the four sections appear in order.
  - [ ] Role checks: the Manager cites no raw-line Evidence and uses no cost, money, downtime, customer or SLA terms outside Unknowns; required Unknowns are named; a count called "Incident(s)" must cite `list_incidents` Evidence.
  - [ ] Side-by-side check (#1): a Team Lead or Manager answer that cites a Log Entry count must also cite Incident and Spread Evidence (`list_incidents` or `top_patterns`).
  - [ ] `verify_answer(role, draft)` reads the Evidence Ledger, including earlier sessions on the same `LEDGER_PATH`, returns `ok`, `problems[]` (each with the offending text and a fix hint) and `checked_numbers`, and never raises on a bad draft.
  - [ ] Self-tests cover at least 10 deliberately broken answers, including the #3 pilot failures (rounding, bracketed ranges, self-computed sums, missing citations).
  - [ ] The description tells the agent to call it before every final answer and to fix every problem it reports.
- **Reuse:** `research/3-NoNaim95:research/3/NoNaim95/harness/assertions.py`; `research/3-OrFrederick:research/3/OrFrederick/harness/checks.py` and `test_checks.py`.

### T13: `export_investigation` and ledger replay
- **Owner:** O. **Area:** answer side. **Blocked by:** T3, T12. **Links:** `docs/decisions.md` Differentiator 8 and "Verification" (replay script), #2 (`get_evidence(rerun=true)` moved here).
- **Goal:** turn a session into a reproducible Markdown report without the tool writing anything, and re-run its ledger to prove that the numbers still hold.
- **Acceptance criteria:**
  - [ ] `export_investigation(role, title, answers[{role, question, answer}], evidence_ids?)` renders, per answering Role, the question, the tool calls with parameters, the SQL, result tables (first 10 rows, totals, `truncated`), the answer, and a closing "What the data does and does not establish" section, and returns the Markdown and its size in the result. It writes nothing and stays read-only.
  - [ ] Its `role` is the audience of the report: for `manager`, raw-line rows are left out of every table (counts, SQL and Data Scope stay); for `engineer` and `team_lead`, tables show the first 10 rows. The description says so, and the worked investigation is exported with `role='engineer'`.
  - [ ] `scripts/export_report.py <ledger.jsonl> <answers.yaml> --role <role> --out <file>` writes the same Markdown to disk.
  - [ ] `scripts/replay_ledger.py <ledger.jsonl> [--answers <file>]` re-runs every ledger statement with its parameters, compares totals for every record and rows only for results not sorted by relevance (BM25 ties are unordered), checks the numbers in the final answers with `verify/checks.py`, and exits non-zero on any mismatch.
  - [ ] A snapshot test renders a fixture ledger; a `db` test replays a recorded ledger successfully and detects a tampered number.

### T14: Pattern catalogue: `list_patterns`, `search_patterns` and the catalogue-first chain
- **Owner:** N. **Area:** tools. **Blocked by:** T5, T6, T9, T27. **Links:** #7 Decision proposal, `docs/decisions.md` round 2 (pattern-first search).
- **Goal:** Pattern-first search. Claude reads the catalogue and picks Pattern ids, and exact SQL counts, retrieves and chains Incidents by those ids.
- **Acceptance criteria:**
  - [ ] `list_patterns(role, dataset, component?)` returns the catalogue (`template_id`, template text, Component, dominant level, entries, first and last seen, `family_id`), paged by Component when a page would exceed about 8k tokens. Its description says to read the catalogue first, to pick no Pattern when nothing is direct evidence, and to state that as an Unknown.
  - [ ] `search_patterns(role, dataset, query, top)` is the lexical fallback: BM25 with stemming over the template text plus a small curated synonym map committed to the repo (`src/serenedb_log_mcp/corpus/synonyms.yaml`). It returns the same columns as `list_patterns` with a score, and zero matches is a hint, not an error.
  - [ ] Integration test of the chain `list_patterns` → `count_by(pattern_ids=[…])` → `search_logs(pattern_ids=[…])` → `list_incidents(pattern_key=…)` on one non-alert BGL Pattern and one OpenStack Pattern: the `count_by` total, the `search_logs` total and the `list_incidents` `entries_total` agree.
  - [ ] `count_by(group_by='pattern')` and `trend` by Pattern work on the template-keyed data.
  - [ ] The descriptions say when to use which: `list_patterns` to choose Patterns for a concept, `search_patterns` when the catalogue is too long to read, `top_patterns` (T28) to rank by frequency in a Data Scope.
- **Reuse:** `research/7-OrFrederick:research/7/OrFrederick/llm_select.py`, `synonyms.yaml`, `labels.yaml`; `research/7-NoNaim95:research/7/NoNaim95/experiments/r7e_synonyms.py`, `r7e_llm_catalog.py`.

### T15: Eval L0: contract tests, golden SQL runner and CI
- **Owner:** O. **Area:** quality. **Blocked by:** T5, T6, T12. **Links:** #3 Decision proposal (L0), brief (Best MCP Server).
- **Goal:** a no-LLM, zero-cost suite that runs on every PR and guards what the Best MCP Server judges look at: descriptions, limits, truncation honesty and safety.
- **Acceptance criteria:**
  - [ ] `tests/contract/` checks every tool in `tools/list`: a required `role`, a title, a description that states purpose, inputs and outputs, the read-only hints (only `create_index` and `drop_index` may set `readOnlyHint=false`), the alwaysLoad meta, enums for closed sets, declared limits and an output schema.
  - [ ] Property tests show `truncated` consistency for every Evidence tool under random limits, and injection strings in every string parameter yield 0 rows or a `ToolError`, never an SQL error.
  - [ ] Ground-truth leak guard: every tool is called with representative inputs on the sample data, and no serialised result contains "abnormal".
  - [ ] `make test-golden` (in `mk/quality.mk`) runs every `corpus` golden test.
  - [ ] A GitHub Actions workflow runs ruff, mypy, unit tests, and the contract and `db` tests against a `serenedb/serenedb` service container loaded with the Loghub 2k samples (`dev_loader.py --sample`, then `make derive` with `SLM_PATTERN_TIER1=0`, so CI needs no Loghub-2.0 download). It is green on the PR.
- **Reuse:** `research/3-NoNaim95:research/3/NoNaim95/harness/test_contract.py`, `harness/ci/eval.yml`; `research/3-OrFrederick:research/3/OrFrederick/ci/eval.yml`.

### T16: `find_precursors` and similar Incidents
- **Owner:** N. **Area:** tools. **Blocked by:** T9, T10, T27. **Links:** `docs/decisions.md` round 2 (precursor mining with a guard), #1 (Cascade), #4 (KERNMC before APPSEV), #7 Decision proposal (similar Incidents by Jaccard).
- **Goal:** relate Incidents to each other. Find which Patterns precede a target Pattern more often than chance, and report them only as Hypotheses that pass stated support and lift thresholds; otherwise say "no consistent precursor found". Find earlier look-alikes of an Incident by exact template-set Jaccard plus Component plus Spread, without embeddings.
- **Acceptance criteria:**
  - [ ] `find_precursors(role, dataset, target_pattern, window_s, same_source, min_support, min_lift, filters)` returns, per candidate precursor, the support (target Incidents preceded), the rate, a baseline rate from a deterministic probe grid, and the lift. Only candidates that pass both thresholds are returned.
  - [ ] Every result is labelled a Hypothesis in `notes`, quotes delays per source (median and range), and states the thresholds and the baseline method.
  - [ ] The defaults follow #1's Cascade rule: `window_s = 60` and a minimum lag of 1 s, so same-timestamp artefacts never count as precursors. Both are stated in the description and in `notes`.
  - [ ] Golden tests: KERNMC within 120 s precedes 64 of 136 APPSEV Incidents (47%), and KERNTERM to KERNREC has a per-node median delay of 85 s (range 47 to 142 s).
  - [ ] A Pattern with no precursor that passes returns the "no consistent precursor found" note, not an empty list without explanation.
  - [ ] `list_incidents(similar_to=<incident_id>)` ranks earlier Incidents by the Jaccard similarity of the Pattern sets seen on the Incident's sources during the Incident, with same-Component and Spread tie-breaks (never a BM25 sort), and states the similarity method in `notes`. A golden test checks one known repeat from the #7 research.
- **Reuse:** `research/1-NoNaim95:…/experiments/05b_cascade.sql`, `literature/cascade_pairs.sql`; `research/1-OrFrederick:…/sql_cascade.sql`; `research/4-OrFrederick:…/precede.sql`, `mc_follow.sql`, `deliberation/chain_verify.sql`; `research/7-OrFrederick:research/7/OrFrederick/similar_incidents.py`, `incidents.sql`; `research/7-NoNaim95:research/7/NoNaim95/experiments/r7e_incidents.py`.

### T17: Eval L1/L2: driver, graders, question set, report
- **Owner:** O. **Area:** quality. **Blocked by:** T7, T11, T12, T14, T15, T27, T28. **Links:** #3 Decision proposal.
- **Goal:** measure, with the client the judges use, whether Claude picks the right tools and grounds every number, and report it with confidence intervals.
- **Acceptance criteria:**
  - [ ] `eval/driver.py` runs `claude -p --output-format stream-json --verbose --mcp-config … --strict-mcp-config --tools "" --setting-sources "" --allowedTools "mcp__serenedb-log__*" --max-budget-usd …` with pinned model ids, a fresh `LEDGER_PATH` per trial, Role prompts invoked as slash commands, and the `CLAUDE_CODE_*` and `CLAUDECODE` variables stripped.
  - [ ] `eval/questions.yaml` has about 40 questions: 10 per Role, 10 adversarial (including the Manager cost question and the "disk full" control from #7), and cross-Role triples, with a tune and holdout split.
  - [ ] L1 graders check must-call and must-not-call, ordered subsequences, argument predicates, only-our-tools and budgets, and separate `infra.tool_errors` from model failures. L2 graders reuse `verify/checks.py`.
  - [ ] `make eval MODEL=… K=…` (in `mk/quality.mk`) writes a report with pass rate, pass^k and Wilson CIs per question. A Haiku k=3 run and a Sonnet k=1 run go to `docs/results/eval.md`.
- **Reuse:** `research/3-NoNaim95:research/3/NoNaim95/harness/driver.py`, `grade.py`, `assertions.py`, `questions.yaml`, `test_eval.py`, `survey/spike/stats.py`; `research/3-OrFrederick:research/3/OrFrederick/harness/run_eval.py`, `agent_sdk_driver.py`, `questions.yaml`.
- **Human-only input:** the Opus 5.5 k=5 README run (about $13), the judge-calibration labels for L3, and the `ANTHROPIC_API_KEY` CI secret are human decisions (#3). The overnight run stops at Haiku and Sonnet pilots.

### T18: Worked investigation via `export_investigation`, pinned by golden SQL
- **Owner:** O. **Area:** deliverable. **Blocked by:** T5, T6, T11, T13, T27, T28. **Links:** #5 Decision proposal and round 2, #4 (worked thread), `docs/decisions.md` Differentiators 4, 6 and 8.
- **Goal:** produce the required worked investigation, "Start-up path failures", for all three Roles from a real Claude session, rendered by `export_investigation` so it can be reproduced, with every number pinned by golden SQL.
- **Acceptance criteria:**
  - [ ] `scripts/run_worked_investigation.py` runs the three Role questions through Claude Code with one shared `LEDGER_PATH`, so the Roles chain through shared Evidence ids, then renders the report with `export_investigation` (`role='engineer'`) and writes `docs/worked-investigation.md` through `scripts/export_report.py`.
  - [ ] The content follows #5: the `/p/gb1` KERNMNTF wave (26,426 Log Entries, 83 Incidents on 12 days, 7 Incidents on all 1,024 I/O nodes); the second Dataset with the 4 of 4 OpenStack slow spawns found by timing while text search finds none, and d7d68893 as a Hypothesis; the KERNDTLB trap, worded "The largest KERNDTLB Incident has 152,182 Log Entries on 1 node (2005-06-11). The category has 152,734 Log Entries."; the Manager answer with two Data Scopes (the wave's window, and the whole BGL Dataset over alert categories with the top-5 band); the refusal on cost with the measured alternative; and what the data does and does not establish, including that the "start-up path" link is thematic, not causal.
  - [ ] At least one answer uses a real aggregation over both Datasets, for example `count_by(group_by='dataset')` or `count_by(group_by='team')` over the whole Base Corpus, and states that Data Scope.
  - [ ] `tests/golden/test_worked_investigation.py` pins every number in the document with independent golden SQL and checks the document against the pinned values.
  - [ ] `verify_answer` passes on all three answers, and `scripts/replay_ledger.py` passes on the committed ledger.
- **Reuse:** `research/5-NoNaim95:research/5/NoNaim95/r5_golden.py`, `findings.md`; `research/5-OrFrederick:research/5/OrFrederick/queries.sql`, `deliberation/v1_incidents.sql`, `deliberation/v5_appsev.sql`, `deliberation/rank_compare_rerun.py`.
- **Note:** the numbers are pinned on the dev loader data. T25 re-pins them if the organisers' loader changes line counts.

### T19: Agent-managed index: `create_index`, `drop_index` and benchmark
- **Owner:** N. **Area:** award track 1. **Blocked by:** T5, T6. **Links:** brief (informal bonus), `docs/decisions.md` (award order, huge datasets), #2 (stretch tools).
- **Goal:** the agent builds an inverted index over a time slice when it needs one and removes it afterwards, and a benchmark shows the trade-off between build time and query time saved.
- **Acceptance criteria:**
  - [ ] `create_index(role, dataset, time_from, time_to)` builds an inverted index over the slice, refreshes it, and returns `build_ms`, rows and the index name. The name is generated server-side, there is at most one agent index per Dataset, and the slice size is capped. First verify on 26.10.1 whether a partial inverted index (`CREATE INDEX … USING inverted (…) WHERE …`) works: the CREATE INDEX reference documents it, but the LangChain FAQ says inverted indexes have no `WHERE`. If it does not work, index a time-sliced view instead.
  - [ ] `drop_index(role, name)` drops only indexes that `create_index` created; anything else raises a `ToolError`. These two tools are the only ones with `readOnlyHint=false`, and they use a separate write connection.
  - [ ] Routing changes only the resolver: `corpus.datasets.relation` returns the agent index when the Data Scope lies inside its slice, no tool module changes, and `notes` and the Data Scope say which index answered.
  - [ ] `bench/index_tradeoff.py` (`make bench-index` in `mk/bench.mk`) measures, for a fixed query set, the time without the index, the build time and the time with the index (medians of 3, environment stated), computes the break-even number of queries, and writes `docs/results/index-tradeoff.md`.
  - [ ] A `db` test covers create, query and drop, and that `drop_index` refuses a non-agent index.
- **Reuse:** `research/2-NoNaim95:…/prototype/index_build.py`; `research/4-OrFrederick:…/rbac_vidx.py` (index over a view).

### T20: Huge datasets: `text_bench` in place, scale report
- **Owner:** N. **Area:** award track 2. **Blocked by:** T19. **Links:** `docs/decisions.md` (huge datasets: query `text_bench/part_000` in place, never index the whole set), #6 (g10 on OTel), brief (show data queried, query times and costs).
- **Goal:** query about 1B rows of remote Parquet in place, let the agent index a time slice on demand, and report how much data was queried, the query times and the cost trade-off.
- **Acceptance criteria:**
  - [ ] `text_bench` (part_000) is registered as a Dataset only in the Dataset registry and resolver, through a view over `read_parquet('<url>')` with a single path. A list of files would make the view generic, and a generic view can be indexed but cannot return real columns. First verify whether renaming to canonical column names inside the indexed view keeps it on the fast path (the "Indexing Views" page allows renaming, but the generic-view error asks for a plain `SELECT *`); if not, index a plain `SELECT *` view and rename in an outer view. `describe_corpus` lists the Dataset as remote and not indexed.
  - [ ] `search_logs`, `count_by` and `trend` work on it without code changes in the tool modules, require a time slice, and turn an unbounded request into a `ToolError` that suggests a slice or `create_index`.
  - [ ] `create_index` over a `text_bench` time slice works; the notes state that the index is a snapshot refreshed by `REINDEX`.
  - [ ] `bench/huge_scale.py` (`make bench-huge`) reports rows (from `EXPLAIN ANALYZE`), file bytes (HTTP HEAD), wall time per query and index build time, and writes `docs/results/huge-datasets.md`. "Cost" is defined explicitly (bytes transferred and wall time); no money figure appears without a cited public price.
  - [ ] Pattern keys for one slice via the g10 macro (distinct messages first) are reported in the same document.
- **Reuse:** `research/6-NoNaim95:…/in_db/exp5a.sql`, `exp5b.sql`.
- **Risk:** remote reads need `enable_external_access`; never `SET` it on the shared instance (it is server-wide).

### T21: Multimodal correlation: `otel_demo` logs, traces, metrics, `get_trace`
- **Owner:** N. **Area:** award track 3. **Blocked by:** T20. **Links:** `docs/decisions.md` (multimodal source: `otel_demo/`, joined on TraceId).
- **Goal:** follow an error Log Entry to its trace and the slow or failing span, with the metrics around it, all as Evidence.
- **Acceptance criteria:**
  - [ ] The `otel_demo` logs, traces and metrics are registered as Datasets in the registry and resolver through `read_parquet` views; the file URLs are recorded in config.
  - [ ] `get_trace(role, trace_id)` returns the trace's spans (service, operation, duration, status), the Log Entries with that TraceId and the metrics in the span window, in the Evidence envelope with limits.
  - [ ] `docs/results/multimodal.md` holds a short investigation from an error Log Entry to its trace and span, with Evidence ids.
  - [ ] The description says when to use `get_trace` instead of `get_context`.

### T22: Access control per Role: views, LOGIN roles
- **Owner:** O. **Area:** award track 4. **Blocked by:** T5, T6, T7. **Links:** #4 Decision proposal (defence in depth), `docs/decisions.md` (access control per Role).
- **Goal:** show Role separation at the database level as defence in depth, while the Role-scoped tools remain the real guarantee, and state the limits honestly.
- **Acceptance criteria:**
  - [ ] `sql/access/roles.sql` creates per-Role views (the Manager's views expose no message text), LOGIN roles `slm_engineer`, `slm_team_lead` and `slm_manager` with passwords from the environment, and `GRANT SELECT` on the views only.
  - [ ] Only the resolver changes: with per-Role DSNs configured (`DATABASE_URL_<ROLE>`, already honoured by T2), `corpus.datasets.relation` returns the Role's views. A test runs every tool as each Role, and the `--role` pin (T3) is shown working in a test.
  - [ ] `db` tests show that the Manager login cannot select from the base tables or the other Roles' views, and that its tools still work.
  - [ ] The README states the limits: SereneDB has no row-level security; any login role can read server files and change some server-wide settings; the grants are defence in depth.
- **Reuse:** `research/4-OrFrederick:research/4/OrFrederick/rbac.py`, `rbac_idx.py`, `rbac_vidx.py`, `deliberation/acl_probe.py`; `research/4-NoNaim95:research/4/NoNaim95/survey/acl_setup.sql`, `acl_test.py`, `acl_test_fts.py`.

### T23: MCP Apps visuals (human check needed)
- **Owner:** O. **Area:** novelty. **Blocked by:** T27. **Links:** `docs/decisions.md` Differentiator 5.
- **Goal:** show an Incident timeline inside Claude Desktop through the MCP Apps extension (`io.modelcontextprotocol/ui`), with the structured result unchanged as the fallback.
- **Acceptance criteria:**
  - [ ] Check the MCP Apps extension spec and `mcp` 2.3.0 support first, and record the findings in the PR.
  - [ ] One UI resource (Incident timeline) is linked from `list_incidents` results; the structured result is unchanged.
  - [ ] An automated test shows that the resource is listed and served and that the tool result references it.
  - [ ] The rack heatmap and the ranked risk table are built only after the timeline is confirmed working.
- **Human-only input:** whether the UI renders over stdio in Claude Desktop. Claude Code does not render MCP Apps, so the overnight run cannot confirm it. Label the issue `blocked-human` after the automated part merges.

### T24: Demo v1 and slides on the core
- **Owner:** O. **Area:** presentation. **Blocked by:** T12, T18. **Links:** `docs/decisions.md` Differentiator 9 (demo priority) and "demo insurance".
- **Goal:** a 5-minute demo of the core that runs live or from a cached replay, and slides whose every number comes from `docs/results/` or the worked investigation, ready before any award-track work. T29 adds the award-track results later.
- **Acceptance criteria:**
  - [ ] `docs/demo/script.md` is a timed run sheet for the core in the decided order: the line-versus-Incident reveal, live self-verification, the ground-truth score, the refusal. Each beat lists the exact prompt and the expected Evidence.
  - [ ] `docs/demo/slides.md` outlines the slides, and every number cites a file in `docs/results/` or `docs/worked-investigation.md`.
  - [ ] `scripts/demo_replay.py` replays a recorded ledger and answers offline, without SereneDB.
  - [ ] A timed dry run of the script against the dev instance stays within 5 minutes; the timing is in the PR.
- **Human-only input:** recording the backup video and designing the final slide deck.

### T25: Swap in the organisers' loader and Ownership Table (human-blocked)
- **Owner:** N. **Area:** data. **Blocked by:** T4 and the organisers' materials (label `blocked-human`). **Links:** `docs/decisions.md` (organiser materials are the source of truth), brief (README must use the supplied loader).
- **Goal:** replace the dev loader path with the supplied loader as the documented route, with no change outside the adapter, the Ownership Table and the README's loading section.
- **Acceptance criteria:**
  - [ ] `sql/adapters/official.sql` maps the organisers' schema to the canonical tables, including neutral OpenStack `source_id` values, and `make derive ADAPTER=official` works.
  - [ ] The organisers' Ownership Table replaces the stub, and every `TODO(organiser)` marker is gone.
  - [ ] The README's loading section uses the supplied loader.
  - [ ] The golden values are re-pinned where line counts differ, the worked investigation is regenerated, and the differences are listed in the PR.
- **Stub that unblocks everything else:** the dev adapter and the stub Ownership Table from T4.

### T26: Stretch: `find_patterns` with `fastembed` vectors for the OTel track
- **Owner:** N. **Area:** huge-datasets track (stretch). **Blocked by:** T14, T20. **Links:** #7 Decision proposal, #6 (about 13k g10 keys from 100M OTel rows), `docs/decisions.md` round 2 ("vector search only if it needs no external embedding key").
- **Goal:** where the Pattern catalogue no longer fits in context (OTel slices with thousands of keys), let Claude find Patterns semantically, using SereneDB's vector search with query embeddings that need no API key. This is also the SereneDB vector and hybrid showcase. It starts only after the core and the index-benchmark track are done.
- **Acceptance criteria:**
  - [ ] Template vectors for an OTel slice's Patterns are computed with `fastembed` (pure pip, no key) and stored in SereneDB; the model download is a documented one-time step, and the tool is disabled with a clear `ToolError` when the optional dependency is missing.
  - [ ] `find_patterns(role, dataset, text, top)` ranks Patterns with exact `<=>` search fused by RRF with BM25 plus synonyms in one statement, and returns the same columns as `list_patterns` with scores.
  - [ ] It is registered only for Datasets whose catalogue exceeds the `list_patterns` page budget; for BGL and OpenStack the description points to `list_patterns`.
  - [ ] A short recall check on a handful of labelled OTel queries goes to `docs/results/huge-datasets.md`, labelled as indicative until a human labels fresh queries (#7).
- **Reuse:** `research/7-OrFrederick:research/7/OrFrederick/embed.py`, `eval_retrieval.py`, `ivf_vs_exact.py`; `research/7-NoNaim95:research/7/NoNaim95/experiments/r7e_vec_sql.py`, `r7e_hybrid_check.py`.

### T27: `list_incidents` tool and OpenStack Request Failure Incidents
- **Owner:** N. **Area:** data and tools. **Blocked by:** T2, T3, T9, T10. **Links:** #1 consolidated Decision proposal, #2 (`list_incidents` parameters), #7 (catalogue-first chain).
- **Goal:** expose Incidents to Claude with Spread and Shape next to the Log Entry counts, and add the OpenStack Request Failure Incidents, which need the template ids from T9.
- **Acceptance criteria:**
  - [ ] `list_incidents(role, dataset, filters, pattern_key, gap_s, merge_scope, sort, limit)` accepts alert categories and `template_id` values as `pattern_key` plus the shared Data Scope filters. Default-parameter calls read the precomputed table; other calls compute over the filtered set only; a non-default call without a narrowing filter raises a `ToolError` that suggests one. The description documents the stable band of 60 to 600 s.
  - [ ] Every result summary carries `entries_total`, `incidents_total` and the Spread totals, so Log Entries, Incidents and Spread appear side by side.
  - [ ] A derive step adds OpenStack Request Failures to `incident`: ERROR or CRITICAL lines and HTTP 5xx responses chained per `template_id`, with instances or hosts as Spread; instances whose lifecycle never completes are listed as Request Failures per instance.
  - [ ] Golden test through the tool: `list_incidents(dataset='bgl', pattern_key='KERNDTLB', sort='entries')` reports the largest Incident with 152,182 Log Entries on 1 node (2005-06-11), and the summary shows 152,734 Log Entries for the category.
  - [ ] The description explains how `pattern_key` relates to `list_patterns` and `alert_category`.
- **Reuse:** `research/1-NoNaim95:research/1/NoNaim95/experiments/06_openstack.sql`; `research/1-OrFrederick:research/1/OrFrederick/sql_os_incidents.sql`, `sql_os_lifecycle.sql`.

### T28: `top_patterns` with Manager priority ranking, Chronic/New flags and drift
- **Owner:** N. **Area:** tools. **Blocked by:** T6, T9, T10, T27. **Links:** #4 Decision proposal (Manager ranking), `docs/decisions.md` round 2 (noise versus signal), #1 (Recurrence, Chronic, New Pattern, template drift).
- **Goal:** rank Patterns by what matters for each Role, and give the Manager the measured top-5 band over a stated scope instead of raw-line volume.
- **Acceptance criteria:**
  - [ ] `top_patterns(role, filters, top, sort)` with `sort` in `entries`, `incidents`, `sources`, `recent`, `priority` returns per Pattern the entries, Incidents, sources, first and last seen, and the cumulative Pareto share.
  - [ ] Chronic and New Pattern flags use stated rules (New Pattern relative to the end of the Data Scope, with a minimum count), and a drift hand-off flag from `family_id` marks a Pattern that ends while a near-identical one starts nearby in time; a golden test covers the EDRAM wording change of 2005-11-15.
  - [ ] `sort='priority'` implements the #4 ranking over a stated filter, BGL alert categories plus OpenStack Request Failure Patterns: the equal-weight mean of percentile ranks of Incidents, Spread (midplanes; instances for OpenStack), active weeks, the 28-day Incident rate against the whole-period rate, and recency, shown as a top-5 band. The Data Scope and `notes` state the filter.
  - [ ] Golden test "whole BGL Dataset, alert categories": the band is {KERNMC, APPSEV, KERNRTSP, KERNMNTF, KERNSOCK}.
  - [ ] The description says to use `top_patterns` to rank and `list_patterns` to choose Patterns for a concept.
- **Reuse:** `research/4-OrFrederick:research/4/OrFrederick/score.py`, `deliberation/rank_verify.py`; `research/4-NoNaim95:research/4/NoNaim95/experiments/r4e_rank.py`; `research/6-NoNaim95:…/in_db/exp5c.sql` (new Patterns); `research/1-NoNaim95:…/experiments/05c_chronic_new.sql`.

### T29: Final pass: demo, slides and README with all results
- **Owner:** O. **Area:** presentation. **Blocked by:** T17, T24. **Links:** `docs/decisions.md` Differentiator 9 (demo priority), brief (Deliverables).
- **Goal:** the last ticket of the run. Bring the demo, slides and README up to date with every result that landed, including the award tracks, without growing the demo past 5 minutes.
- **Acceptance criteria:**
  - [ ] The README links every file present in `docs/results/` and the worked investigation, and its "SereneDB features used" and limitations sections match what was built.
  - [ ] The demo script and slides add the award-track beats that landed (visuals if T23 is confirmed, the index trade-off from T19, the 1B-row result from T20 as a slide with numbers, not a live wait) and stay within 5 minutes.
  - [ ] `docs/tools.md` is regenerated, and the eval report is refreshed with the final tool set (Haiku k=3).
  - [ ] A fresh-context subagent follows the README on a clean clone once more and reaches a working `tools/list`.
- **Note:** run it last; if an award track merges afterwards, re-run it as a `follow-up`.

---

## 5. Ownership

| Area | Owner | Tickets | Files owned |
|---|---|---|---|
| Build, data, SQL, aggregations, Patterns, Incidents, scale tracks | NoNaim95's Claude | T1, T2, T4, T6, T9, T10, T11, T14, T16, T19, T20, T21, T25, T26, T27, T28 | `pyproject.toml` (initial), `Makefile`, `mk/data.mk`, `mk/bench.mk`, `db.py`, `scope.py`, `corpus/`, `sql/adapters`, `sql/derive`, `sql/seed`, `scripts/{dev_loader,fetch_data,derive,gen_pattern_sql,score_patterns,ground_truth}.py`, `bench/`, the tool modules of its tickets, `docs/results/{patterns,ground-truth,index-tradeoff,huge-datasets,multimodal}.md` |
| MCP surface contract, answer side, retrieval tracer bullet, eval, docs, demo | OrFrederick's Claude | T3, T5, T7, T8, T12, T13, T15, T17, T18, T22, T23, T24, T29 | `evidence.py`, `ledger.py`, `toolkit.py`, `instructions.py`, `roles/`, `verify/`, `export/`, `sql/access`, `mk/quality.mk`, `mk/docs.mk`, `eval/`, `tests/contract`, `.github/workflows`, `scripts/{replay_ledger,export_report,gen_tool_docs,run_worked_investigation,demo_replay}.py`, `README.md`, `docs/tools.md`, `docs/worked-investigation.md`, `docs/results/eval.md`, `docs/demo/`, the tool modules of its tickets |

This follows the suggested split with one change: **T5 (the retrieval tracer bullet) goes to OrFrederick**. NoNaim95 holds the critical path (T4, T9, T10, T27, T28), and T5 is the first consumer of the envelope that OrFrederick defines in T3. The shared contracts are defined once (plan section 2.7): `evidence.py` and the ledger by OrFrederick in T3, the database layer and Data Scope builder by NoNaim95 in T2 (which is therefore blocked by T3), and the resolver by NoNaim95 in T4. Each side builds against the other's merged contract, not a draft.

OrFrederick's Claude was absent at the end of Phase 1, so the leader may end up implementing most tickets. The split still holds: an absent Claude's unstarted tickets are taken over (overnight protocol, section 3), and its files stay organised by area, which keeps reviews and later hand-backs simple.

Rules that keep the two Claudes out of each other's files:
- Tools are auto-discovered, so adding a tool never edits `server.py` or a shared list.
- Tools never name tables or indexes; they call `corpus.datasets.relation`. T19, T20 and T22 change only the resolver, and a contract test (T4) enforces it.
- The root `Makefile` belongs to T1 plus T4's one-line include; every later target goes into the area's `mk/<area>.mk`.
- Only OrFrederick edits `README.md`; NoNaim95's results go to `docs/results/*.md`, which the README links.
- Dependencies are added to `pyproject.toml` in the PR that needs them; conflicts there are resolved by re-running `uv lock`.
- `docs/tools.md` is generated; conflicts are resolved by re-running `make docs`.
- `tests/conftest.py` belongs to T1; later fixtures go into per-directory `conftest.py` files.

---

## 6. Open points and assumptions

### Assumptions made overnight (each needs human confirmation)
1. **Tool set (#2):** the plan adopts OrFrederick's Decision proposal (12 tools plus 2 stretch tools) and adds the tools that later Decision proposals call for: `get_role_brief` (#4), `list_patterns` and `search_patterns` (#7), `list_incidents(similar_to=…)` (#7, a parameter rather than a tool), `get_trace` as the multimodal stretch tool, and `find_patterns` as the OTel stretch tool (#7). That makes 15 core tools. Evidence: #4 and #7 recommend these tools and the #2 proposal predates them.
2. **Binding and crashes (#2 round 2):** psycopg's default binding, `ClientCursor` behind a switch (default off), no BM25 sort with a tie-break key, and reconnect plus one retry with a note. Both Claudes agreed on OrFrederick's Decision proposal; these amendments are NoNaim95's alone and not yet confirmed by OrFrederick. Evidence: 182 executions on native 26.10.1, where the two-key BM25 sort crashed 28 of 70 times regardless of binding mode and the single-key sort 0 of 44.
3. **Overlap among `list_patterns`, `search_patterns` and `top_patterns`:** kept as three tools with explicit "use X instead" lines, as the #7 Decision proposal says, because their outputs differ (a catalogue, a lexical search, a ranking with Incidents). The L1 eval (T17) measures confusion. If it is high, fold `search_patterns` into a `query` parameter of `list_patterns`.
4. **`role` on every tool (#4, as decided):** `get_evidence` shapes the stored record for the Role (the Manager gets no raw rows), and `export_investigation`'s `role` is the audience of the report (for the Manager, raw-line rows are left out; counts, SQL and Data Scope stay). No override of #4 is needed.
5. **Manager and raw lines (#4):** for the Manager, `search_logs`, `get_context` and `get_evidence` withhold rows and keep totals and facets, instead of raising an error. Evidence: #4 says a Manager answer "cannot cite raw lines it never got".
6. **`export_investigation` is read-only:** it returns the Markdown in the result, and scripts write the file (`scripts/export_report.py`, `scripts/run_worked_investigation.py`). So `create_index` and `drop_index` are the only tools with `readOnlyHint=false`.
7. **Template mining (#6, leader alone):** the plan follows the Decision proposal (Tier 1 SQL `CASE` chain from Loghub-2.0 with strict wildcards first, Tier 2 generic g10 masks, a dictionary per distinct message). The Loghub-2.0 files are downloaded by `scripts/fetch_data.py` and turned into the Tier 1 SQL at derive time; neither is committed. Tier 1 is on by default and `SLM_PATTERN_TIER1=0` turns it off, so a "no" from the humans on the licence costs one environment variable. The merge gate is agreement of at least 99.99% on covered lines; the target is 99.9999%, as measured in #6. Both are logged in `docs/overnight-decisions.md`.
8. **Canonical term:** "Pattern" is the concept and `template_id` the column (#6 Decision proposal); "template text" is the text of a Pattern; "Pattern Family" names the drift merge (`family_id`). Proposed in `GLOSSARY.md` under "Proposed terms".
9. **Embeddings (#7, leader alone):** the catalogue path (`list_patterns`, with `search_patterns` as the fallback) is the core. The optional vector `find_patterns` for the OTel track is the stretch ticket T26, with `fastembed` for query embeddings (no key), after the core and the index-benchmark track.
10. **Manager ranking scope (#4):** the priority ranking covers a stated filter, BGL alert categories plus OpenStack Request Failure Patterns, and the Data Scope says so. The #4 and #5 numbers (the top-5 band) were measured on the whole BGL Dataset over alert categories, not on the whole Base Corpus.
11. **Ledger across sessions:** one JSONL file at `LEDGER_PATH`, loaded at startup, numbering continued, appends under an advisory file lock, so Evidence ids stay valid across server restarts and parallel processes.
12. **Ground-truth hygiene:** OpenStack source files get neutral ids, and tests assert that no canonical column and no tool output contains "abnormal". Labels are read only by `scripts/ground_truth.py`.
13. **Time zones:** all time arithmetic, including `trend` buckets and baselines, uses UTC (`epoch_utc` for BGL), and the Data Scope says so (#1, #65; section 2.6). `count_by`, `trend` and `search_logs` take and report times in UTC (#73), and `describe_corpus` states each Dataset's range in UTC; raw Log Entries still show `ts` as logged. Superseded: the first version bucketed on `ts` as logged, which put two hours into one BGL bucket on 2005-10-30.
14. **Parameters (#1):** `gap_s` and `merge_scope` stay tool parameters with the documented stable band; the Manager ranking weights stay fixed and equal (#4 recommendation).
15. **Access control (#4):** presented as defence in depth with the limits stated in the README.
16. **Tool names:** no prefix; the server name `serenedb-log` already namespaces them in Claude Code.
17. **Shared dev instance:** implementers isolate derived tables with `SLM_SCHEMA`, and never run experiments that change server-wide settings.
18. **CI data:** CI uses the Loghub 2k samples, downloaded at run time, and runs the derive step with `SLM_PATTERN_TIER1=0`; golden numbers are checked locally against the full Base Corpus (`make test-golden`), because loading 4.7M lines in CI is slow.
19. **Makefile:** per-area includes (`mk/*.mk`) rather than one shared file, so parallel PRs rarely conflict.

### Small decision items dropped or moved
- **#7's hand-made OpenStack family map:** not planned. BGL uses its alert categories, and both Datasets use the `family_id` drift merge. A follow-up ticket if Team Lead answers need OpenStack families.
- **#1's Episode** (an optional merge of Incidents across Patterns): not planned; the term is only proposed in the glossary.
- **#4's cross-Role worked thread** (KERNMC → KERNRTSP → APPSEV on R27-M0-NA-C:J11-U11, 2006-01-03): replaced by #5's "Start-up path failures" investigation (T18), which both Claudes later agreed on. The KERNMC-before-APPSEV pair survives as a precursor golden test in T16.
- **#2's `get_evidence(rerun=true)`:** moved to `scripts/replay_ledger.py` (T13), which compares totals for relevance-sorted results and rows otherwise.
- **#1's rule that every Team Lead and Manager answer shows Log Entries, Incidents and Spread side by side:** enforced as a `verify_answer` check (T12) and carried in every `list_incidents` summary (T27).

### Real conflicts noted
- **Partial inverted indexes:** the CREATE INDEX reference documents `CREATE INDEX … USING inverted (…) WHERE …`, while the LangChain client FAQ says SereneDB inverted indexes have no `CREATE INDEX … WHERE`. T19 verifies on 26.10.1 before relying on it, and falls back to an index over a time-sliced view.
- **Remote Parquet lists:** `docs/decisions.md` says to pass a list of URLs because HTTPS globs are unlikely to work. The docs say a list of files makes a view generic: a generic view can still be indexed and answers counts and scores, but it cannot return real columns. T20 uses one file per view, and verifies whether renaming to canonical columns inside the indexed view keeps the fast path (the "Indexing Views" page allows renaming, while the generic-view error message asks for a plain `SELECT *`), or renames in an outer view.
- **Precursor baseline (#4):** the two Claudes measured different baselines for KERNMC before APPSEV (0.16% of 5,152 hourly probes against 0.33% of 3,618). T16 fixes one deterministic probe grid and documents it.
- **`COPY … TO` and `ATTACH` for a Manager login (#4):** they succeeded on 26.09.1.3 and were denied on 26.10.1. T22 re-checks on the version in use.
- **The `line_no` filter bug (#2):** OrFrederick saw a `line_no` predicate make the index ignore the `ts` filter on 26.09.1.3 with an index over a view; NoNaim95 could not reproduce it on 26.10.1 with a base-table index. The plan keeps point lookups on the base table either way, and the bug report should name the version and the view.
- **Earlier concern that `verify_answer` was not in `docs/decisions.md` (#4 round 2):** resolved; `main` now lists it as Differentiator 3.

### Human decisions still outstanding
- **#1:** confirm the Incident definition and the glossary terms; confirm the README wording "the published counts reproduce exactly at T = 300 s; the paper's text states 5 s"; keep `gap_s` and `merge_scope` as parameters or fix them.
- **#2:** confirm `verify_answer`, `export_investigation` and `find_precursors` and their names; confirm NoNaim95's round-2 amendments, which OrFrederick has not confirmed; find out which SereneDB version the organisers run (dictionary DDL, crash risk); whether to report the SereneDB findings to the organisers (the two-key BM25 sort crash on 26.10.1 with `crash_discriminate.py` as the minimal repro; the `line_no` filter bug on 26.09.1.3 over a view; a SELECT-only role can read files; some `SET`s are server-wide); tool-name prefix; HTTP hosting for judges.
- **#3:** the headline model for the README (proposed: Opus 5.5 with Sonnet 5.5 beside it); who labels about 40 transcripts for the L3 judge; the eval budget (about $13 for one Opus k=5 run) and whether to run nightly; packaging as a Claude Code plugin; the `ANTHROPIC_API_KEY` CI secret.
- **#4:** present access control as defence in depth or skip it; fixed or parameterised ranking weights; who checks Claude Desktop by hand (prompt menu, `instructions`, `structuredContent`); re-check RLS on the demo SereneDB version.
- **#5:** the organisers' SereneDB version (full-text dictionary DDL); the two-Data-Scope Manager answer with a top-5 band on stage; whether the KERNDTLB trap and the `get_context` cameo fit in 5 minutes; whether the thematic cross-Dataset link is enough for minimum requirement 2 (T18 adds a real aggregation over both Datasets, and the README says the link is not causal).
- **#6:** whether we may ship or use the Loghub-2.0 template files (licence, and whether they count as a resource listed in the brief), and with it the Tier 1 default; "Pattern" or "Template" as the canonical glossary term; whether the untested `family_id` suffix rule is enough or family membership should be a reviewed table.
- **#7:** ship the optional `find_patterns` vector tool for the OTel track (T26) or drop it; commit the curated synonym map (recommended); have a human label about 20 fresh queries before README retrieval numbers are quoted.
- **Plan-level:** judge install (`.mcpb` bundle or hosted endpoint, parked in `docs/decisions.md`); whether MCP Apps render over stdio in Claude Desktop (T23); the demo backup video (T24).
