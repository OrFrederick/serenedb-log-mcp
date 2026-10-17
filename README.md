# serenedb-log-mcp

An MCP server that turns large log corpora into answers for three Roles: an Engineer, a Team Lead and a Manager. SereneDB does the searching and the counting. Claude calls the tools, reads the returned Evidence and explains it.

It is our entry for the SereneDB challenge "Making Sense of Logs: The Right Insights for the Right People" at the National AI Hackathon 2026. The Base Corpus is BGL (4,747,963 lines, BlueGene/L, 2005-06 to 2006-01) and OpenStack (207,820 lines, 2017-05).

- [How it works](#how-it-works)
- [Setup](#setup): SereneDB, data, loading, derive
- [Connect Claude](#connect-claude): Claude Code, Claude Desktop
- [Use it](#use-it)
- [Tools](#tools) and the full [tool reference](docs/tools.md)
- [Results](#results)
- [SereneDB features used](#serenedb-features-used)
- [Known SereneDB caveats](#known-serenedb-caveats)
- [Limitations](#limitations) and [Status](#status)
- [Development](#development), [Data and citations](#data-and-citations)

## How it works

The three Roles ask different questions and get different Evidence, not just longer or shorter answers:

| Role | Asks | Receives | Next Step |
|---|---|---|---|
| Engineer | What failed, where, and what happened just before and after? | Raw Log Entries, their context, exact counts | a check to run |
| Team Lead | Which problems recur over a period, and which Components and Teams do they involve? | Counts, trends with baselines, at most 3 sample Log Entries per search | an investigation priority |
| Manager | Which observed problems deserve attention first? | Measured frequency and affected Components only, no raw Log Entries, and a list of what the logs do not measure (cost, downtime, customer impact) | a prioritised action |

The workflow, from raw files to an answer:

```
loader (organisers' loader: TODO(organiser); until then scripts/dev_loader.py)
   -> raw tables in SLM_RAW_SCHEMA (default public)
   -> sql/adapters/<adapter>.sql           the only loader-specific SQL
   -> scripts/derive.py, sql/derive/*.sql  canonical tables, Pattern dictionary, OpenStack
                                           instance phases, inverted indexes, in SLM_SCHEMA (default slm)
   -> MCP tools                            fixed SQL templates, bound parameters, read-only transactions
   -> Evidence                             SQL, parameters, Data Scope, total_matches, returned,
                                           truncated, query_ms, rows, summary, notes
   -> Evidence Ledger (E1, E2, ...)        every result is stored under an id
   -> Role shaping                         what this Role may receive, its answer frame
   -> Claude                               cites [E<n>] next to every number and answers in four
                                           sections: Observed Facts, Hypotheses, Unknowns, Next Step
```

Principles that run through every tool:

- **Search and counting both run in SereneDB.** `search_logs` and `get_context` return a few example Log Entries; `count_by` and `trend` return exact counts. Every result says how many units matched (`total_matches`), how many it returned and whether it is `truncated`, so a sample is never read as a frequency.
- **Log Entries are not Incidents.** One failing node can write 152,182 lines about one problem. The tools count Log Entries and say so; the Incident model is planned (see [Status](#status)).
- **Every number has its query.** Each result carries the SQL that ran, the bound parameters, the Data Scope (Datasets, time range, filters) and the query time, and is stored in the Evidence Ledger, so an answer can cite `[E3]` and anyone can fetch E3 again with `get_evidence`.
- **Baselines on counts.** `count_by` and `trend` compare each count with the previous period or the median week and flag it as `normal`, `unusual_high`, `unusual_low` or `no_baseline`.
- **Read-only.** Every statement runs in `BEGIN READ ONLY` ... `ROLLBACK`, with a client-side timeout.

## Setup

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/), to run SereneDB locally (or access to an existing SereneDB).
- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.13 (`.python-version`) and every dependency; you do not need Python yourself.
- `make` and `git`.
- [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) or [Claude Desktop](https://claude.ai/download).
- For the full Base Corpus: internet access to zenodo.org (about 156 MB to download) and about 2.2 GB of free disk space for the extracted files.

### 1. Clone and install

```bash
git clone https://github.com/OrFrederick/serenedb-log-mcp
cd serenedb-log-mcp
uv sync
```

Run every command below from the repository root.

### 2. Start SereneDB

```bash
make db-up
```

This starts a container named `serenedb-log-mcp-db` on port 7890 with trust authentication and waits until it answers `SELECT 1`. It is the same as:

```bash
docker run -d --name serenedb-log-mcp-db -p 7890:7890 \
  -e SERENEDB_HOST_AUTH_METHOD=trust serenedb/serenedb:26.10.1
```

On Apple Silicon and other arm64 hosts, `make db-up` uses `serenedb/serenedb:26.09.1.3` on `linux/arm64` instead, because 26.10.1 is published for amd64 only and crashes under emulation (see [caveats](#known-serenedb-caveats)). Override with `SERENEDB_IMAGE=... SERENEDB_PLATFORM=...`. `make db-down` removes the container.

Already have a SereneDB, for example the organisers' instance? Skip this step and point `DATABASE_URL` at it (a libpq connection string or a `postgresql://` URI). The default is `host=localhost port=7890 dbname=postgres user=postgres`, which matches `make db-up`.

### 3. Download the data

```bash
make fetch-data
```

Downloads BGL and OpenStack (Loghub, [Zenodo 8196385](https://zenodo.org/records/8196385)) and the Loghub-2.0 template annotations ([Zenodo 8275861](https://zenodo.org/records/8275861)) into `./data` (`SLM_DATA_DIR`), checks sizes and md5 sums, and extracts only the files it needs. It is idempotent: a second run downloads nothing.

### 4. Load the Base Corpus

#### With the organisers' loader: TODO(organiser)

The brief says the organisers supply a loader, a schema and an Ownership Table. **We have not received them yet.** When they arrive (ticket T25), this section will say how to run the supplied loader into `SLM_RAW_SCHEMA`, and step 5 becomes `make derive ADAPTER=official`. Until then `sql/adapters/official.sql` is a stub that lists the columns it must produce, and `make derive ADAPTER=official` stops with a message instead of running.

#### Fallback: the dev loader

```bash
make load-dev
```

`scripts/dev_loader.py` is a throwaway loader we wrote so the project runs now; it is not the organisers' loader. It reads `./data` and creates four raw tables in `SLM_RAW_SCHEMA` (default `public`): `bgl`, `openstack`, and the Loghub-2.0 label tables `lh2_bgl` and `lh2_openstack`. It never drops an existing table unless you pass `LOAD_ARGS=--replace`, and each table is swapped in only after it loaded completely. BGL has 4.7 million lines, so this step is the slowest one.

### 5. Derive

```bash
make derive
```

Builds everything the tools read, inside `SLM_SCHEMA` (default `slm`): the canonical tables `bgl_entry` and `os_entry`, the Pattern dictionary (`pattern_dict`, `pattern`), the OpenStack instance phases (`os_instance`), the stub Ownership Table (`ownership`) and the inverted indexes. On the full Base Corpus it took 14.3 s on our SereneDB 26.10.1 instance ([docs/results/patterns.md](docs/results/patterns.md)). It is re-runnable and prints a report with row counts at the end.

Patterns come in two tiers: Tier 1 matches the Loghub-2.0 templates and needs the files from step 3; `SLM_PATTERN_TIER1=0 make derive` uses only the generic masks (Tier 2) and needs no Loghub-2.0 files.

### Quick variant: the 2,000-line samples

To try the server in a few minutes, without the full download, load the Loghub 2k samples instead of steps 3 to 5:

```bash
make load-sample                 # downloads the two 2k samples on first use, about 5 s
SLM_PATTERN_TIER1=0 make derive  # about 3 s
```

The answers then describe 2,000 lines per Dataset, not the Base Corpus. `make load-sample` refuses to overwrite a table with more than 2,000 rows, so it cannot replace a full load by accident. To keep the sample apart from a full load, give it its own schemas: `export SLM_RAW_SCHEMA=sample_raw SLM_SCHEMA=sample` before both commands and before starting Claude.

## Connect Claude

The server speaks MCP over stdio (default) or streamable HTTP. It is registered as `serenedb-log`, so Claude Code shows its tools as `mcp__serenedb-log__<tool>`. It reads its configuration from the environment ([Configuration](#configuration)); the defaults match the setup above.

### Claude Code

The repository's [`.mcp.json`](.mcp.json) registers `serenedb-log` (stdio, `uv run serenedb-log-mcp`) and the SereneDB docs server `serenedb-docs`. Start Claude Code in the repository root:

```bash
claude
```

Approve the project MCP servers when Claude Code asks. `/mcp` then lists `serenedb-log` as connected with its tools. Claude Code passes its environment to the server, so export `DATABASE_URL`, `SLM_SCHEMA` and the other variables before you start `claude` if you changed them.

To register the server for use outside the repository, give uv the absolute path of the checkout:

```bash
claude mcp add serenedb-log \
  -e DATABASE_URL="host=localhost port=7890 dbname=postgres user=postgres" \
  -- uv --directory /absolute/path/to/serenedb-log-mcp run serenedb-log-mcp
```

Headless, for scripts and checks:

```bash
claude -p "As the engineer: how many BGL Log Entries are there? Cite the Evidence." \
  --mcp-config .mcp.json --strict-mcp-config --allowedTools mcp__serenedb-log
```

(`--allowedTools` takes several values, so put the prompt before it.)

### Claude Desktop

Add the server to `claude_desktop_config.json` (Settings, Developer, Edit Config; on macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, on Windows `%APPDATA%\Claude\claude_desktop_config.json`) and restart Claude Desktop:

```json
{
  "mcpServers": {
    "serenedb-log": {
      "command": "/absolute/path/to/uv",
      "args": ["--directory", "/absolute/path/to/serenedb-log-mcp", "run", "serenedb-log-mcp"],
      "env": {
        "DATABASE_URL": "host=localhost port=7890 dbname=postgres user=postgres",
        "SLM_SCHEMA": "slm"
      }
    }
  }
}
```

Claude Desktop does not read your shell profile, so use absolute paths (`which uv` prints the one for uv) and put every variable you need into `env`.

### Other options

- **HTTP:** `uv run serenedb-log-mcp --transport http --port 8000` serves streamable HTTP at `http://127.0.0.1:8000/mcp` (`--host` changes the bind address). Register it with `claude mcp add --transport http serenedb-log http://127.0.0.1:8000/mcp`.
- **One Role per server:** `--role engineer|team_lead|manager` pins a server to one Role: its tools refuse every other Role, and it offers only that Role's prompt.
- `uv run serenedb-log-mcp --help` lists the flags and the environment variables.

## Use it

Tell Claude which Role you are, or start from a Role prompt. In Claude Code the prompts are slash commands; each argument is one optional token:

```
/mcp__serenedb-log__engineer [dataset] [focus]     focus: a node, alert category, Component, request or entry id
/mcp__serenedb-log__team_lead [dataset] [period]   period: 30d, 2w, 24h, 90min
/mcp__serenedb-log__manager [dataset] [period]
```

Questions to try:

- Engineer: "What failed in the first /p/gb1 mount failure on BGL, and what happened just before it?"
- Engineer: "What happened on node R27-M0-NA-C:J11-U11 around 03:25 on 2006-01-03?"
- Team Lead: "Which problems recurred over the last 30 days of BGL, and which Teams own them?"
- Team Lead: "Is the KERNMNTF mount failure rising or falling week by week?"
- Manager: "Which observed problems deserve attention first?"
- Manager: "What did these failures cost us?" (the answer says the logs do not measure cost and offers the measured alternative)

Claude calls `describe_corpus` first, then searches and counts. Each answer cites `[E<n>]` next to every number and has four sections: Observed Facts, Hypotheses, Unknowns and Next Step. Relative times such as "the last 30 days" resolve against each Dataset's last Log Entry, not today, and the Data Scope says so.

`make run` starts the server over stdio by hand; it then waits for an MCP client on stdin.

## Tools

Every tool takes a required `role`, is read-only, and returns its result in `structuredContent`. [docs/tools.md](docs/tools.md) documents each tool's description, inputs, limits and output fields; it is generated from the live registry with `make docs`, and a test fails when it is stale.

| Tool | Purpose | Limits |
|---|---|---|
| `describe_corpus` | What data exists: per Dataset its size, time range, corpus end, Components with Teams, levels, alert categories, Patterns. Call it first. | one row per value |
| `search_logs` | Example Log Entries by full-text query (all words, phrase, any words, fuzzy) or by Pattern ids, with time, Component, level and source filters; exact total and facets. | 20 rows by default, at most 50 |
| `get_context` | The Log Entries just before and after one entry, on the same node, request or instance, or in a time window. | 10 per side by default, at most 50 |
| `count_by` | Exact counts grouped by Dataset, Component, Team, level, node, rack, midplane, alert category or Pattern, with a baseline per count; works over the whole Base Corpus. | 25 groups by default, at most 100, plus an `(other)` row |
| `trend` | Zero-filled counts per time bucket, optionally one series per group, with baselines. | at most 5 series and 400 buckets |
| `latency_stats` | OpenStack API latency percentiles, spawn and build durations per instance, Latency Degradation and Phase-Duration Anomalies. | at most 50 rows |
| `get_evidence` | Fetch a stored Evidence record by id, shaped for the Role asking now. | one record |
| `get_role_brief` | The playbook of a Role, for clients that do not show MCP prompts. | |

Messages in rows are cut to 240 characters. Query text goes through `websearch_to_tsquery` and is always a bound parameter; there is no free-form SQL tool.

## Results

- [docs/results/ground-truth.md](docs/results/ground-truth.md): `latency_stats` finds **4 of 4** labelled OpenStack slow spawns by timing alone (5 flagged at the default threshold; the fifth is reported as a Hypothesis). No ERROR or WARNING line points at them, so text search alone would not find them.
- [docs/results/patterns.md](docs/results/patterns.md): the SQL Pattern dictionary agrees with the Loghub-2.0 labels on 99.99991% of BGL lines (4,631,257 of 4,631,261) and on 100% of OpenStack lines, with 465 BGL and 73 OpenStack Patterns.

## SereneDB features used

Search and analytics run in the same engine and often in the same statement: `count_by(query=...)` and `trend(query=...)` filter with the inverted index and group in one SQL statement.

| Feature | Where we use it | Docs |
|---|---|---|
| Inverted index (`CREATE INDEX ... USING inverted`) over every canonical column | `sql/derive/90_indexes.sql`; every search and every filtered count reads it | [Inverted index](https://serenedb.com/docs/sql/indexes/inverted), [syntax](https://serenedb.com/docs/sql/statements/create_index/inverted) |
| Text search dictionary `split_text(case := 'lower')` with frequency, position, offset and norm | the `log_text` dictionary on `message` | [CREATE TEXT SEARCH DICTIONARY](https://serenedb.com/docs/sql/statements/create_text_search_dictionary), [split_text](https://serenedb.com/docs/sql/functions/search/tokenizers/split_text) |
| `@@` with `websearch_to_tsquery` (all words, the default), `phraseto_tsquery` (phrase), a bare string (any words) and `ts_levenshtein` (fuzzy) | `search_logs`, and the `query` filter of `count_by` and `trend` (`src/serenedb_log_mcp/fts.py`) | [websearch_to_tsquery](https://serenedb.com/docs/sql/functions/search/full-text#websearch_to_tsquery), [phraseto_tsquery](https://serenedb.com/docs/sql/functions/search/full-text#phraseto_tsquery), [ts_levenshtein](https://serenedb.com/docs/sql/functions/search/full-text#ts_levenshtein) |
| BM25 relevance, `BM25(idx.tableoid)` | `search_logs(sort='relevance')` | [BM25](https://serenedb.com/docs/sql/functions/search/scoring#bm25) |
| Range and set filters on the index: `ts_ge`, `ts_lt`, `ts_any`, with `::score(NULL)` so filters do not change the score (`ts_between` is the closed-range form of the same family) | time, Component, level, source and Pattern filters in `search_logs` | [ts_between and range queries](https://serenedb.com/docs/sql/functions/search/full-text#ts_between), [per-node score control](https://serenedb.com/docs/sql/functions/search/scoring#per-node) |
| `ts_highlight` | matched words marked in `search_logs` rows | [Highlighting](https://serenedb.com/docs/sql/functions/search/highlighting#virtual-column) |
| `VACUUM (REFRESH_TABLE)` | publishes the eventually consistent index after the derive | [VACUUM](https://serenedb.com/docs/sql/statements/vacuum#refreshing--refresh_), [index lifecycle](https://serenedb.com/docs/sql/indexes/inverted#lifecycle) |
| `time_bucket`, gap-filled with `generate_series` | `trend`, the weekly baselines, `latency_stats` buckets | [time_bucket](https://serenedb.com/docs/sql/functions/timestamp#time_bucketbucket_width-timestamp-offset) |
| Window functions (`row_number() OVER`, `sum() OVER (PARTITION BY ...)`) | the top-N plus `(other)` row of `count_by`, the Pattern dictionary, neutral OpenStack source ids | [Window functions](https://serenedb.com/docs/sql/functions/window_functions) |
| Macros (`CREATE OR REPLACE MACRO`) | the Pattern masks and the Tier 1 template chains, stored in `SLM_SCHEMA` | [CREATE MACRO](https://serenedb.com/docs/sql/statements/create_macro) |
| `regexp_full_match` (RE2) | Tier 1 Pattern assignment, in one SQL step over distinct messages | [regexp_full_match](https://serenedb.com/docs/sql/functions/regular_expressions#regexp_full_matchstring-regex-options) |
| `quantile_cont`, `median` | latency percentiles and the robust z-scores of Phase-Duration Anomalies | [Statistical aggregates](https://serenedb.com/docs/sql/functions/aggregates#statistical-aggregates) |
| Remote Parquet (`read_parquet('https://...')`) with an index over a slice | in review: huge datasets (T20, #62) and OTel traces (T21, #75), not on `main` yet | [Indexing external data](https://serenedb.com/docs/sql/indexes/inverted/external-data#supported-sources) |
| Postgres wire protocol | psycopg 3 on port 7890 | [Run with Docker](https://serenedb.com/docs/installation/docker) |

## Known SereneDB caveats

What we measured on SereneDB 26.10.1 (and 26.09.1.3 where noted) and how the server copes. Details and measurements are in [docs/overnight-decisions.md](docs/overnight-decisions.md) and issue [#2](https://github.com/OrFrederick/serenedb-log-mcp/issues/2).

- **`statement_timeout` is accepted but not enforced.** The database layer starts a timer and calls `cancel_safe()` after `SLM_QUERY_TIMEOUT_S` (default 15 s); the connection stays usable.
- **`SET default_transaction_read_only` is not enforced.** Every statement runs in an explicit `BEGIN READ ONLY` ... `ROLLBACK`, which SereneDB does enforce (INSERT and CREATE fail with SQLSTATE 25006).
- **Intermittent SIGSEGV.** A BM25 sort with a tie-break key after the score (`ORDER BY BM25(...) DESC, id`) crashed native 26.10.1 in 28 of 70 runs; the single-key sort crashed in 0 of 44. A crash can surface on a later, innocent query. So no SQL template sorts by BM25 plus a second key (ties are unordered), and on a lost connection the database layer waits up to 30 s for `SELECT 1`, retries the statement once and adds a note to the result. A second failure becomes an error that suggests `sort='time'`.
- **Digit-hyphen-digit terms fail with XX000** ("Attempted to dereference shared_ptr that is NULL!") when a TSQUERY term on a verbatim-indexed column contains a digit, a hyphen and a digit, as many OpenStack instance UUIDs do. Such filter values fall back to a plain `= ANY(%s::VARCHAR[])` on the same index relation, which returns the same rows and leaves the BM25 scores unchanged.
- **Bound timestamps in `generate_series` fail** with "Invalid unicode (byte sequence mismatch) detected in value construction" under server-side binding, in the table-function form `FROM generate_series(%s::TIMESTAMP, ...)`; `col = ANY(%s)` with a Python list fails too. The templates use `unnest(generate_series(...))`, `unnest(%s::VARCHAR[])` and `= ANY(%s::VARCHAR[])`, which work in both binding modes.
- **A plain SQL filter on the index relation adds to the BM25 score** (+2.0 per filter). Filters are TSQUERY predicates with `::score(NULL)`, so they never change the relevance order.
- **The inverted index is eventually consistent.** The derive step runs `VACUUM (REFRESH_TABLE)` after building it.
- **26.09.1.3, the newest arm64 image**, differs from 26.10.1:
  - `CREATE TEXT SEARCH DICTIONARY ... AS split_text(...)` does not exist; `make derive` reads `version()` and uses the legacy `(TEMPLATE = 'text', ...)` form.
  - A cancel is ignored while a table function such as `generate_series` takes a bound parameter. Every result that ran past `SLM_QUERY_TIMEOUT_S` carries a note with its real duration, and `SLM_CLIENT_BINDING=1` (client-side binding) restores cancellation.
  - A temporary view over a table cannot be read (InsufficientPrivilege), so the adapter uses temporary tables.
  - 26.10.1 is published for amd64 only and crashes under emulation on Apple Silicon, which is why `make db-up` picks 26.09.1.3 on arm64. The Pattern step has been measured on 26.10.1 only.
- **No row-level security** ("Row-level security is not supported"). Access control per Role, if built (T22), uses per-Role views and LOGIN roles.

## Limitations

- **The organisers' loader, schema and Ownership Table have not arrived.** Loading uses our dev loader, and Teams come from a stub Ownership Table with fictional Teams (`team_source = 'stub'` on every row); `describe_corpus` and every count grouped by Team carry a note that says so. T25 swaps in the organisers' materials.
- **No Incidents yet.** The tools count Log Entries exactly and never call them Incidents, but the Incident model (chains of one Pattern within a gap, with Spread and Shape) and the tools built on it (`list_incidents`, `top_patterns`) are not merged yet. `verify_answer` and `export_investigation` are not merged either; see [Status](#status).
- **The worked investigation, the eval report and the demo are not written yet** (T18, T17, T24).
- **Time zones.** BGL `ts` is shown as logged, in US Pacific local time; every gap and window uses `epoch_utc`. OpenStack timestamps carry no zone and are taken as UTC. Timestamps with a time zone are rejected rather than shifted silently.
- **One Dataset per search and trend.** BGL and OpenStack are 12 years apart and their BM25 scores are not comparable, so `search_logs`, `get_context` and `trend` take one Dataset; `count_by` and `describe_corpus` cover both.
- **What the logs cannot say.** Cost, downtime, customer impact and SLA breaches are not in the logs; the Manager's results list them as `not_measured`, and a root cause stays a Hypothesis unless the Evidence establishes it.
- **The Evidence Ledger is in memory** unless `LEDGER_PATH` points to a file; then it is shared across sessions and server processes.
- **Tier 1 Patterns use the Loghub-2.0 template files**, which are downloaded at setup and never committed. Whether that counts as a "resource listed in the brief" is open; `SLM_PATTERN_TIER1=0` uses the generic masks only.
- **The local stdio route is the documented one.** No hosted endpoint is provided.

## Status

Built overnight on 2026-10-16/17 from the plan in [docs/plan.md](docs/plan.md); the humans have not yet confirmed the overnight decisions ([docs/overnight-decisions.md](docs/overnight-decisions.md)).

- **On `main`:** project skeleton, database layer, Evidence contract and ledger, corpus adapter and derive step, Pattern dictionary, `describe_corpus`, `search_logs`, `get_context`, `count_by`, `trend`, `latency_stats`, `get_evidence`, `get_role_brief`, the Role layer, prompts and server instructions.
- **In review (open pull requests):** `verify_answer` (T12, #69), `list_patterns` and `search_patterns` (T14, #70), agent-managed `create_index` and `drop_index` with a benchmark (T19, #59), huge datasets over remote Parquet (T20, #62), multimodal `get_trace` (T21, #75), trend buckets on UTC (#74), database layer follow-ups (#58).
- **Planned:** the default Incident table and `list_incidents` (T10, T27), `top_patterns` with the Manager ranking (T28), `export_investigation` (T13), `find_precursors` (T16), the worked investigation (T18), the eval harness (T15, T17), access control per Role (T22), MCP Apps visuals (T23), the demo (T24), the organisers' loader (T25).

## Configuration

All settings come from the environment; `uv run serenedb-log-mcp --help` lists them.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `host=localhost port=7890 dbname=postgres user=postgres` | SereneDB connection (libpq string or `postgresql://` URI) |
| `DATABASE_URL_ENGINEER`, `DATABASE_URL_TEAM_LEAD`, `DATABASE_URL_MANAGER` | unset | per-Role connections; fall back to `DATABASE_URL` |
| `SLM_SCHEMA` | `slm` | schema of the derived tables the tools read |
| `SLM_RAW_SCHEMA` | `public` | schema of the loader's raw tables |
| `SLM_DATA_DIR` | `./data` | downloaded files |
| `SLM_QUERY_TIMEOUT_S` | `15` | client-side query timeout in seconds |
| `SLM_CLIENT_BINDING` | `0` | `1` binds parameters on the client (`psycopg.ClientCursor`) |
| `LEDGER_PATH` | unset (in memory) | JSONL file of the Evidence Ledger |
| `SLM_EXPORT_DIR` | `./exports` | where investigation exports go (used once `export_investigation` lands) |
| `SLM_PATTERN_TIER1` | `1` | `0` derives Patterns without the Loghub-2.0 files |

## Development

```bash
make help        # every target
make check       # lint, typecheck, pure tests (no database)
make test-db     # tests against SereneDB at DATABASE_URL; they load the 2k samples into throwaway schemas
make test-corpus # golden numbers on the full Base Corpus (needs make load-dev)
make docs        # regenerate docs/tools.md after changing a tool
make score-patterns                            # score the Patterns against the Loghub-2.0 labels
uv run python scripts/ground_truth.py openstack  # rewrite the OpenStack section of docs/results/ground-truth.md
```

Tests that need a database skip with the reason when none is reachable; `SLM_REQUIRE_DB=1` turns those skips into failures. Work is ticket based: see [docs/plan.md](docs/plan.md), the GitHub issues, [GLOSSARY.md](GLOSSARY.md) for the canonical terms and [docs/decisions.md](docs/decisions.md).

## Data and citations

The datasets come from Loghub and are downloaded at setup, never committed. Loghub permits use for research or academic work and asks for these citations:

- Loghub: Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. "Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics." IEEE International Symposium on Software Reliability Engineering (ISSRE), 2023. [arXiv:2008.06448](https://arxiv.org/abs/2008.06448), [github.com/logpai/loghub](https://github.com/logpai/loghub).
- BGL: Adam J. Oliner, Jon Stearley. "What Supercomputers Say: A Study of Five System Logs." IEEE/IFIP International Conference on Dependable Systems and Networks (DSN), 2007, pp. 575-584. [doi:10.1109/DSN.2007.103](https://doi.org/10.1109/DSN.2007.103).
- Loghub-2.0 (template annotations used for Tier 1 Patterns): Zhihan Jiang, Jinyang Liu, Junjie Huang, Yichen Li, Yintong Huo, Jiazhen Gu, Zhuangbin Chen, Jieming Zhu, Michael R. Lyu. "A Large-scale Evaluation for Log Parsing Techniques: How Far Are We?" ACM SIGSOFT International Symposium on Software Testing and Analysis (ISSTA), 2024. [arXiv:2308.10828](https://arxiv.org/abs/2308.10828).
