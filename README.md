# serenedb-log-mcp

An MCP server that turns large log corpora into answers for three Roles: an Engineer, a Team Lead and a Manager. SereneDB does the searching and the counting. Claude calls the tools, reads the returned Evidence and explains it.

It is our entry for the SereneDB challenge "Making Sense of Logs: The Right Insights for the Right People" at the National AI Hackathon 2026. The Base Corpus is BGL (4,747,963 lines, BlueGene/L, 2005-06 to 2006-01) and OpenStack (207,820 lines, 2017-05).

- [How it works](#how-it-works): Roles, Log Entries and Incidents, Evidence
- [Setup](#setup): SereneDB, data, loading, derive
- [Connect Claude](#connect-claude): Claude Code, Claude Desktop; [Configuration](#configuration)
- [Use it](#use-it)
- [Tools](#tools) and the full [tool reference](docs/tools.md)
- [Self-verifying answers and reproducible reports](#self-verifying-answers-and-reproducible-reports)
- [Agent-managed index](#agent-managed-index), [Access control per Role](#access-control-per-role), [Visual Evidence (MCP Apps)](#visual-evidence-mcp-apps)
- [Worked investigation](#worked-investigation)
- [Results](#results)
- [SereneDB features used](#serenedb-features-used)
- [Known SereneDB caveats](#known-serenedb-caveats)
- [Limitations](#limitations)
- [Development](#development), [Data and citations](#data-and-citations)

## How it works

The three Roles ask different questions and get different Evidence, not just longer or shorter answers:

| Role | Asks | Receives | Next Step |
|---|---|---|---|
| Engineer | What failed, where, and what happened just before and after? | Raw Log Entries, their context, exact counts, Incidents with Spread | a check to run |
| Team Lead | Which problems recur over a period, and which Components and Teams do they involve? | Counts, trends with baselines, Incidents and Recurrence, at most 3 sample Log Entries per search | an investigation priority |
| Manager | Which observed problems deserve attention first? | Measured frequency and affected Components only, the priority band of `top_patterns`, no raw Log Entries, and a list of what the logs do not measure (cost, downtime, customer impact) | a prioritised action |

The workflow, from raw files to an answer:

```
loader (the organisers' loader once it arrives; until then scripts/dev_loader.py)
   -> raw tables in SLM_RAW_SCHEMA (default public)
   -> sql/adapters/<adapter>.sql           the only loader-specific SQL
   -> scripts/derive.py, sql/derive/*.sql  canonical tables, Pattern dictionary, default Incident
                                           table, OpenStack instance phases, inverted indexes,
                                           in SLM_SCHEMA (default slm)
   -> MCP tools                            fixed SQL templates, bound parameters, read-only transactions
   -> Evidence                             SQL, parameters, Data Scope, total_matches, returned,
                                           truncated, query_ms, rows, summary, notes
   -> Evidence Ledger (E1, E2, ...)        every result is stored under an id
   -> Role shaping                         what this Role may receive, its answer frame
   -> Claude                               cites [E<n>] next to every number, answers in four sections
                                           (Observed Facts, Hypotheses, Unknowns, Next Step) and
                                           checks the draft with verify_answer before it answers
```

Principles that run through every tool:

- **Search and counting both run in SereneDB.** `search_logs` and `get_context` return a few example Log Entries; `count_by`, `trend`, `top_patterns` and `list_incidents` return exact counts. Every result says how many units matched (`total_matches`), how many it returned and whether it is `truncated`, so a sample is never read as a frequency.
- **Log Entries are not Incidents.** One failing node can write 152,182 lines about one problem ([docs/results/ground-truth.md](docs/results/ground-truth.md)). An Incident is a maximal chain of Log Entries of one Pattern where each follows the previous one within 300 s (merged across sources by default), and each BGL Incident carries its Spread (nodes, node cards, midplanes, racks, the top node's share) and a Shape (single-node storm, single-node isolated, one midplane, multi-midplane, machine-wide). The default Incident table is built at derive time; other gaps and filters are chained at query time. `list_incidents` and `top_patterns` report Log Entries and Incidents as separate columns, and `verify_answer` rejects a count called "Incidents" that does not cite them. OpenStack Incidents are Request Failures (ERROR or CRITICAL lines, HTTP 5xx, instance lifecycles that never complete).
- **Every number has its query.** Each result carries the SQL that ran, the bound parameters, the Data Scope (Datasets, time range, filters) and the query time, and is stored in the Evidence Ledger, so an answer can cite `[E3]` and anyone can fetch E3 again with `get_evidence`.
- **Baselines on counts.** `count_by` and `trend` compare each count with a named period, the previous period or the median week and flag it as `normal`, `unusual_high`, `unusual_low` or `no_baseline`.
- **Read-only.** Every query runs in `BEGIN READ ONLY` ... `ROLLBACK`, with a client-side timeout. The two exceptions are `create_index` and `drop_index`, which build and drop agent indexes on a short-lived write connection of their own ([Agent-managed index](#agent-managed-index)).

## Setup

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/), to run SereneDB locally. Not needed if you use an existing SereneDB, such as the organisers' instance: then set `DATABASE_URL` (step 2).
- [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs Python 3.13 (`.python-version`) and every dependency; you do not need Python yourself.
- `make` and `git`.
- [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) or [Claude Desktop](https://claude.ai/download).
- For the full Base Corpus: internet access to zenodo.org (about 156 MB to download) and about 2.2 GB of free disk space for the extracted files.

### 1. Clone and install

```bash
git clone https://github.com/OrFrederick/serenedb-log-mcp
cd serenedb-log-mcp
uv sync          # or: make sync
```

Run every command below from the repository root. `make help` lists every target.

### 2. Start SereneDB

```bash
make db-up
```

This starts a container named `serenedb-log-mcp-db` on port 7890 with trust authentication and waits until it answers `SELECT 1`. It is the same as:

```bash
docker run -d --name serenedb-log-mcp-db -p 7890:7890 \
  -e SERENEDB_HOST_AUTH_METHOD=trust serenedb/serenedb:26.10.1
```

On Apple Silicon and other arm64 hosts, `make db-up` uses `serenedb/serenedb:26.09.1.3` on `linux/arm64` instead, because 26.10.1 is published for amd64 only and crashes under emulation (see [caveats](#known-serenedb-caveats)). Override with `SERENEDB_IMAGE=... SERENEDB_PLATFORM=...`, and the port with `SERENEDB_PORT=...`. `make db-down` removes the container.

Already have a SereneDB, for example the organisers' instance? Skip this step and point `DATABASE_URL` at it (a libpq connection string or a `postgresql://` URI). The default is `host=localhost port=7890 dbname=postgres user=postgres`, which matches `make db-up`.

### 3. Download the data

```bash
make fetch-data
```

Downloads BGL and OpenStack (Loghub, [Zenodo 8196385](https://zenodo.org/records/8196385)) and the Loghub-2.0 template annotations ([Zenodo 8275861](https://zenodo.org/records/8275861)) into `./data` (`SLM_DATA_DIR`), checks sizes and md5 sums, and extracts only the files it needs. It is idempotent: a second run downloads nothing.

### 4. Load the Base Corpus

#### With the organisers' loader (not available yet)

`TODO(organiser)`: the brief says the organisers supply a loader, a schema and an Ownership Table. **We have not received them.** When they arrive (ticket T25, #37), this section will say how to run the supplied loader into `SLM_RAW_SCHEMA`, and step 5 becomes `make derive ADAPTER=official`. Until then `sql/adapters/official.sql` is a stub that lists the columns it must produce, and `make derive ADAPTER=official` stops with a message instead of running.

#### Fallback: the dev loader

```bash
make load-dev
```

`scripts/dev_loader.py` is a throwaway loader we wrote so the project runs now; it is not the organisers' loader. It reads `./data` and creates four raw tables in `SLM_RAW_SCHEMA` (default `public`): `bgl`, `openstack`, and the Loghub-2.0 label tables `lh2_bgl` and `lh2_openstack`. It never drops an existing table unless you pass `LOAD_ARGS=--replace`, and each table is swapped in only after it loaded completely. BGL has 4.7 million lines, so this step is the slowest one.

### 5. Derive

```bash
make derive
```

Builds everything the tools read, inside `SLM_SCHEMA` (default `slm`): the canonical tables `bgl_entry` and `os_entry`, the Pattern dictionary (`pattern_dict`, `pattern`), the default Incident table (`incident`, BGL and OpenStack Request Failures), the OpenStack instance phases (`os_instance`), the stub Ownership Table (`ownership`) and the inverted indexes. It is re-runnable and prints a report with row counts at the end. Measured on the full Base Corpus: the steps up to the indexes took 14.3 s ([docs/results/patterns.md](docs/results/patterns.md)), and the Incident step took 44.0 s ([docs/results/ground-truth.md](docs/results/ground-truth.md)); both move with the load on the instance.

Patterns come in two tiers: Tier 1 matches the Loghub-2.0 templates and needs the files from step 3; `SLM_PATTERN_TIER1=0 make derive` uses only the generic masks (Tier 2) and needs no Loghub-2.0 files.

If you use [access control per Role](#access-control-per-role), run `make access` again after every `make derive`.

### Quick variant: the 2,000-line samples

To try the server in a few minutes, without the full download, load the Loghub 2k samples instead of steps 3 to 5. Give the sample its own schemas, so it never mixes with a full load in `public` and `slm`:

```bash
export SLM_RAW_SCHEMA=sample_raw SLM_SCHEMA=sample
make load-sample                 # downloads the two 2k samples on first use; about 5 to 10 s
SLM_PATTERN_TIER1=0 make derive
```

`make load-sample` prints one line per table, `sample_raw.bgl: 2000 rows in ...` and `sample_raw.openstack: 2000 rows in ...`, and `make derive` ends with a report that starts with `sample.bgl_entry: 2,000 rows` and `sample.os_entry: 2,000 rows`. Start Claude from the same shell, so the server sees the same `SLM_SCHEMA`; Claude then reports 2,000 BGL Log Entries, not the Base Corpus. Without the `export`, the sample goes into `public` and `slm`; `make load-sample` refuses to overwrite a table with more than 2,000 rows, so it can never replace a full load by accident.

## Connect Claude

The server speaks MCP over stdio (default) or streamable HTTP. It is registered as `serenedb-log`, so Claude Code shows its tools as `mcp__serenedb-log__<tool>`. It reads its configuration from the environment ([Configuration](#configuration)); the defaults match the setup above.

### Claude Code

The repository's [`.mcp.json`](.mcp.json) registers `serenedb-log` (stdio, `uv run serenedb-log-mcp`) and the SereneDB docs server `serenedb-docs`. Start Claude Code in the repository root:

```bash
claude
```

Interactive Claude Code asks once whether to trust the project's MCP servers; approve them. `/mcp` then lists `serenedb-log` as connected with 17 tools. Claude Code passes its environment to the server, so export `DATABASE_URL`, `SLM_SCHEMA` and the other variables before you start `claude` if you changed them.

To use the server from any directory, register it in your user scope and give uv the absolute path of the checkout (without `--scope user` it is registered for the current directory only):

```bash
claude mcp add --scope user serenedb-log \
  -e DATABASE_URL="host=localhost port=7890 dbname=postgres user=postgres" \
  -- uv --directory /absolute/path/to/serenedb-log-mcp run serenedb-log-mcp
```

Headless, for scripts and a quick check, from the repository root. First the tool list, without calling any tool:

```bash
claude -p "List the exact names of the serenedb-log tools, one per line. Do not call them." \
  --mcp-config .mcp.json --strict-mcp-config
```

It should print 17 names, from `mcp__serenedb-log__count_by` to `mcp__serenedb-log__verify_answer` (the [Tools](#tools) table). Then a question that calls a tool:

```bash
claude -p "As the engineer: how many BGL Log Entries are there? Cite the Evidence." \
  --mcp-config .mcp.json --strict-mcp-config --allowedTools mcp__serenedb-log
```

The answer cites an Evidence id such as `[E1]`. Headless mode cannot ask you to approve a tool call, so `--allowedTools mcp__serenedb-log` is required for the server's tools to run; without it Claude sees the tools but every call is denied. The flag takes several values, so put the prompt before it. `.mcp.json` also starts the SereneDB docs server over the internet; it is not needed for the log tools, and if it cannot connect the log tools still work.

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

Claude Desktop does not read your shell profile, so use absolute paths (`which uv` prints the one for uv) and put every variable you need into `env`. Claude Desktop is also where the [Incident timeline](#visual-evidence-mcp-apps) can render.

### Other options

- **HTTP:** `uv run serenedb-log-mcp --transport http --port 8000` serves streamable HTTP at `http://127.0.0.1:8000/mcp` (`--host` changes the bind address). Register it with `claude mcp add --transport http serenedb-log http://127.0.0.1:8000/mcp`.
- **One Role per server:** `--role engineer|team_lead|manager` pins a server to one Role: its tools refuse every other Role, and it offers only that Role's prompt.
- `uv run serenedb-log-mcp --help` lists the flags and the environment variables. `make run` starts the server over stdio by hand; it then waits for an MCP client on stdin.

## Use it

Tell Claude which Role you are, or start from a Role prompt. In Claude Code the prompts are slash commands; each argument is one optional token:

```
/mcp__serenedb-log__engineer [dataset] [focus]     focus: a node, alert category, Component, request or entry id
/mcp__serenedb-log__team_lead [dataset] [period]   period: 30d, 2w, 24h, 90min
/mcp__serenedb-log__manager [dataset] [period]
```

Clients that do not show MCP prompts get the same playbook from `get_role_brief(role)`.

Questions to try:

- Engineer: "What failed in the first /p/gb1 mount failure on BGL, and what happened just before it?"
- Engineer: "What usually happens on a node in the minute before a KERNMNTF Incident?" (`find_precursors`)
- Engineer: "Which OpenStack instances spawned unusually slowly, and is there an error line for them?"
- Team Lead: "Which problems recurred over the last 30 days of BGL, and which Teams own them?"
- Team Lead: "Is the KERNMNTF mount failure rising or falling week by week, and how many Incidents is that?"
- Manager: "Which observed problems deserve attention first?"
- Manager: "What did these failures cost us?" (the answer says the logs do not measure cost and offers the measured alternative)

Claude calls `describe_corpus` first, then searches and counts. Each answer cites `[E<n>]` next to every number and has four sections: Observed Facts, Hypotheses, Unknowns and Next Step. Relative times such as "the last 30 days" resolve against each Dataset's last Log Entry, not today, and the Data Scope says so.

## Tools

17 tools. Every tool takes a required `role` and returns its result in `structuredContent`. [docs/tools.md](docs/tools.md) documents each tool's description, inputs, limits and output fields; it is generated from the live registry with `make docs`, and a test fails when it is stale.

| Tool | Purpose | Limits |
|---|---|---|
| `describe_corpus` | What data exists: per Dataset its size, time range, corpus end, Components with Teams, levels, alert categories, Patterns. Call it first. | one row per value |
| `search_logs` | Example Log Entries by full-text query (all words, phrase, any words, fuzzy) or by Pattern ids, with time, Component, level and source filters; exact total and facets. | 20 rows by default, at most 50 |
| `get_context` | The Log Entries just before and after one entry, on the same node, request or instance, or in a time window. | 10 per side by default, at most 50 |
| `count_by` | Exact counts grouped by Dataset, Component, Team, level, node, rack, midplane, alert category or Pattern, with a baseline per count; works over the whole Base Corpus. | 25 groups by default, at most 100, plus an `(other)` row |
| `trend` | Zero-filled counts per time bucket, optionally one series per group, with baselines and Pattern Family hand-offs. | at most 5 series and 400 buckets |
| `list_patterns` | The Pattern catalogue of a Dataset (template text, counts, Component); Claude reads it to choose Pattern ids for a concept. | paged |
| `search_patterns` | Lexical fallback over the catalogue: BM25 with stemming plus a committed synonym map. | see [docs/tools.md](docs/tools.md) |
| `top_patterns` | Patterns ranked by Log Entries (Pareto view), Incidents, sources, recency or `priority`, with Chronic, New and wording-drift flags. `sort='priority'` is the Manager ranking: the equal-weight mean of percentile ranks of Incidents, Spread, active weeks, 28-day Incident trend and recency, with the top 5 per Dataset shown as a band, not an order. | 20 per Dataset by default, at most 50 |
| `list_incidents` | Incidents with Spread and Shape next to the Log Entry counts; other gaps and per-source chaining at query time; `similar_to` finds the earlier Incidents most like one (exact Jaccard of Pattern sets, then Component, then Spread). | 20 by default, at most 100 |
| `find_precursors` | Patterns that precede a target Pattern's Incidents within a window, on the same source by default, with support, lift against a probe baseline and delays. Every row is a Hypothesis; when nothing passes `min_support` and `min_lift`, it says "no consistent precursor found". | 20 by default, at most 50 |
| `latency_stats` | OpenStack API latency percentiles, spawn and build durations per instance, Latency Degradation per time bucket and Phase-Duration Anomalies by robust z-score. | at most 50 rows |
| `create_index` | Build an agent index: a partial inverted index over one time slice of one Dataset, used automatically by later calls inside the slice. Not read-only. | one per Dataset, at most 1,000,000 Log Entries |
| `drop_index` | Drop an agent index that `create_index` built; refuses every other index. Not read-only. | one index |
| `get_evidence` | Fetch a stored Evidence record by id, shaped for the Role asking now. | one record |
| `verify_answer` | Check a draft answer against the Evidence Ledger before it is sent ([below](#self-verifying-answers-and-reproducible-reports)). Runs no query. | drafts up to 50,000 characters |
| `export_investigation` | Render the investigation as a Markdown report: questions, tool calls, SQL, results and answers. Returns the Markdown and writes no file. | up to 20 answers |
| `get_role_brief` | The playbook of a Role, for clients that do not show MCP prompts. | |

Messages in rows are cut to 240 characters. Query text is always a bound parameter; there is no free-form SQL tool.

<!-- TODO(#38): add the `find_patterns` row here once T26 merges, then regenerate docs/tools.md (`make docs`) and update the tool count (17) above and in Connect Claude. -->

## Self-verifying answers and reproducible reports

- **`verify_answer(role, draft)`** checks a draft before Claude sends it: every `[E<n>]` must exist in the Evidence Ledger; every number must appear exactly in the Evidence its sentence cites (never rounded or added up by hand, unless the sentence is marked "(derived from [E1],[E2])" and the number recomputes); the four sections must all be there, in order; a count called Incidents must cite `list_incidents` or `top_patterns`. For the Team Lead and the Manager, Log Entry counts must stand next to Incident Evidence, and cost or downtime belong in Unknowns. For the Manager, no raw-line Evidence may be cited, and Unknowns must name cost, downtime and customer impact. The server instructions and the Role playbooks tell Claude to call it until `ok` is true.
- **`export_investigation`** renders the final answers and the Evidence behind them as Markdown: per Role the question, every tool call with its Data Scope, the SQL with its bound parameters, the result tables, the answer with its `verify_answer` check, and a closing section "What the data does and does not establish". With `role='manager'` it leaves raw Log Entry rows out of every table.
- **Writing and replaying a report.** Set `LEDGER_PATH` to a file so the ledger survives the session, then:

  ```bash
  uv run python scripts/export_report.py <ledger.jsonl> <answers.yaml> --role engineer [--out <file>]
  uv run python scripts/replay_ledger.py <ledger.jsonl> [--answers <answers.yaml>]
  ```

  `export_report.py` writes the tool's own Markdown to `SLM_EXPORT_DIR` (default `exports/`); no database is needed. `replay_ledger.py` re-runs every stored statement against SereneDB and compares totals, Data Scope, summary and rows with what was recorded (exit 0 when everything holds, 1 on any mismatch); with `--answers` it also checks every number in the answers with the same checks as `verify_answer`.

## Agent-managed index

Claude can build an index for the slice it is drilling into and drop it afterwards. `create_index(role, dataset, time_from, time_to)` runs `CREATE INDEX ... USING inverted (...) WHERE ts >= ... AND ts < ...` and `VACUUM (REFRESH_TABLE)` on a separate, short-lived write connection (`DATABASE_URL`, never a per-Role login), checks that the index holds exactly the slice, and reports `build_ms`. Slices are given in UTC like every Data Scope. Every later full-text call, such as `search_logs`, `count_by` or `trend`, whose Data Scope lies inside the slice reads the agent index automatically and names it in its notes and `data_scope.agent_indexes`; the counts are the same as from the full index, only the time changes. At most one agent index exists per Dataset, a slice holds at most 1,000,000 Log Entries, and `drop_index` refuses every index it did not build.

`make bench-index` measures the trade-off and writes [docs/results/index-tradeoff.md](docs/results/index-tradeoff.md); the numbers are in [Results](#results).

## Access control per Role

SereneDB has no row-level security, so `make access` builds the next best thing as defence in depth: per Role a schema `<SLM_SCHEMA>_<role>` with one view per derived table, a LOGIN role that may read only that schema, and a full-text index over the Engineer's and the Team Lead's entry views. The Manager's views have no `message` column at all, so the Manager's login cannot read Log Entry text in any form. Set `DATABASE_URL_ENGINEER`, `DATABASE_URL_TEAM_LEAD` and `DATABASE_URL_MANAGER` (their `user=` and `password=` name the logins to create), then:

```bash
make access         # create the views and LOGIN roles, then check them as each login
make access-check   # repeat only the check
make access-drop    # remove everything again
```

A server started with the same environment connects each Role through its own login; no tool changes. The Manager's `search_logs`, `get_context` and `count_by`/`trend` with `query` are then refused before any statement runs. The Role shaping withholds raw Log Entries from the Manager whether or not the logins are configured, and that stays the guarantee. What the grants do not stop (any login can read server files, view definitions are public, trust auth accepts any password, agent indexes are not per Role) is in [docs/access-control.md](docs/access-control.md), with the measurements.

## Visual Evidence (MCP Apps)

`list_incidents` links an Incident timeline (`ui://serenedb-log/incident-timeline.html`, MCP Apps extension `io.modelcontextprotocol/ui`): one lane per Pattern on a time axis, with `entries_total`, `incidents_total` and the Evidence id. Hosts that support MCP Apps (Claude Desktop, claude.ai) render it next to the result; every other client, including Claude Code, reads the same `structuredContent` and text as before. **Human check pending:** nobody has yet confirmed that the timeline renders in Claude Desktop over stdio (#35), so treat it as unverified. The rack heatmap and ranked risk table wait on that check.

## Worked investigation

[docs/worked-investigation.md](docs/worked-investigation.md) answers "Start-up path failures" for all three Roles, exported by `export_investigation` from the Evidence Ledger in `docs/worked-investigation/`. Every number in it cites its Evidence, and `tests/golden/test_worked_investigation.py` pins them with independent SQL. The headline findings, as the report states them:

- **BGL `/p/gb1` mount failures (KERNMNTF):** 26,426 Log Entries in 83 Incidents (gap 300 s), starting on 12 distinct days; 7 Incidents reached all 1,024 I/O nodes.
- **OpenStack slow spawns:** nova's spawn timing finds all four benchmark-labelled instances (the four slowest of 2,066, all on host os-3), while full-text search for errors on them returns 0 Log Entries.
- **The Manager's trap:** the largest KERNDTLB Incident has 152,182 Log Entries on 1 node, yet the category has 37 Incidents and ranks outside the priority band.

`make worked-investigation-export` re-renders the report without a model, `make worked-investigation-replay` re-runs every stored statement, and `make worked-investigation` runs the three Roles again through `claude -p`.

## Results

Every number here is copied from the file cited next to it; the files say how they were produced and how to reproduce them.

- **BGL: the published failure counts reproduced** ([docs/results/ground-truth.md](docs/results/ground-truth.md)). Chaining the 348,460 expert-tagged alerts per category at `gap_s = 300` gives **1,202 Incidents**, the 1,202 distinct failures Oliner & Stearley published, and 9 of the 11 published per-category counts exactly. What that does not establish is stated there too. The 300 s was fitted on the published total, so only the per-category breakdown is independent. The paper's text says T = 5 s, which gives 1,643, and that 300 s is the intended value is our inference. The check scores the filtering, not alert detection.
- **Log Entries against Incidents** ([docs/results/ground-truth.md](docs/results/ground-truth.md)). The largest Incident of the corpus has 152,182 Log Entries on 1 node (R30-M0-N9-C:J16-U01) on 2005-06-11, a single-node storm. The KERNDTLB category has 152,734 Log Entries and 37 Incidents. The 1,202 alert Incidents split into 720 single-node isolated, 226 multi-midplane, 136 machine-wide, 119 one midplane and 1 single-node storm.
- **OpenStack: slow spawns found by timing** ([docs/results/ground-truth.md](docs/results/ground-truth.md)). `latency_stats(metric='spawn', group_by='instance')` finds **4 of 4** labelled anomalous instances, ranked 1 to 4. It flags 5 at the default min_z 5 (precision 4/5); the fifth, `d7d68893-...`, is reported as a Hypothesis. Recall is 4 of 4 at min_z 3.5, 5 and 8, but the default cut was chosen with the labels in view. None of the labelled instances' Log Entries is above INFO, so text search alone would not find them.
- **Pattern dictionary** ([docs/results/patterns.md](docs/results/patterns.md)). The SQL Pattern assignment agrees with the Loghub-2.0 labels on 99.99991% of BGL lines (4,631,257 of 4,631,261) and on 100% of OpenStack lines (207,632), with 465 BGL and 73 OpenStack Patterns. The derive up to the indexes took 14.3 s.
- **Agent index trade-off** ([docs/results/index-tradeoff.md](docs/results/index-tradeoff.md)). On a BGL slice of 310,868 Log Entries, `create_index` took 426.4 ms until usable. Mean server time per full-text query was 12.4 ms without an inverted index, 21.7 ms with the Dataset's full index and 6.3 ms with the agent index. The build repays itself after 70 queries against a scan and after 28 against the full index; grouped counts by source pay off fastest (7 queries). Through the tools, `count_by(group_by='node', query='lustre mount failed')` took 468.6 ms without and 145.9 ms with the agent index, with identical numbers. Fetching example rows saved nothing. These are timings on a shared instance: compare the modes within the report, not across hosts.
- **Access control** ([docs/access-control.md](docs/access-control.md)). On the full Base Corpus `make access` took 14.1 s and all 18 of its checks passed; each Role's login returned the same counts, and the Manager's text calls were refused.
- **Eval (pending)** ([docs/results/eval.md](docs/results/eval.md)). The L1 (tool choice) and L2 (grounding) harness is built and tested offline, but no model run is recorded yet: a run calls a real model and costs money, and the maintainer will run it. With the Base Corpus in `SLM_SCHEMA` and Claude Code logged in:

  ```bash
  make eval MODEL=claude-haiku-4-5-20251001 K=3   # writes eval/runs/<model>-k<k>/report.md and a section of docs/results/eval.md
  make eval-dry-run                               # prints every claude -p command, no model call
  make eval-report                                # grades an existing run again, no model call
  ```

## SereneDB features used

Search and analytics run in the same engine and often in the same statement: `count_by(query=...)`, `trend(query=...)` and `list_incidents(query=...)` filter with the inverted index and group in one SQL statement.

| Feature | Where we use it | Docs |
|---|---|---|
| Inverted index (`CREATE INDEX ... USING inverted`) over every canonical column | `sql/derive/90_indexes.sql`; every search and every filtered count reads it | [Inverted index](https://serenedb.com/docs/sql/indexes/inverted), [syntax](https://serenedb.com/docs/sql/statements/create_index/inverted) |
| Partial inverted index (`... WHERE ts >= ... AND ts < ...`) | agent indexes from `create_index` (`src/serenedb_log_mcp/index_admin.py`) | [CREATE INDEX (inverted)](https://serenedb.com/docs/sql/statements/create_index/inverted) |
| Text search dictionary `split_text(case := 'lower')` with frequency, position, offset and norm | the `log_text` dictionary on `message` | [CREATE TEXT SEARCH DICTIONARY](https://serenedb.com/docs/sql/statements/create_text_search_dictionary), [split_text](https://serenedb.com/docs/sql/functions/search/tokenizers/split_text) |
| `@@` with `websearch_to_tsquery` (all words, the default), `phraseto_tsquery` (phrase), a bare string (any words) and `ts_levenshtein` (fuzzy) | `search_logs`, and the `query` filter of `count_by`, `trend`, `top_patterns` and `list_incidents` (`src/serenedb_log_mcp/fts.py`) | [websearch_to_tsquery](https://serenedb.com/docs/sql/functions/search/full-text#websearch_to_tsquery), [phraseto_tsquery](https://serenedb.com/docs/sql/functions/search/full-text#phraseto_tsquery), [ts_levenshtein](https://serenedb.com/docs/sql/functions/search/full-text#ts_levenshtein) |
| BM25 relevance, `BM25(idx.tableoid)` | `search_logs(sort='relevance')`, `search_patterns` | [BM25](https://serenedb.com/docs/sql/functions/search/scoring#bm25) |
| Range and set filters on the index: `ts_ge`, `ts_lt`, `ts_any`, with `::score(NULL)` so filters do not change the score | time, Component, level, source and Pattern filters in `search_logs` | [ts_between and range queries](https://serenedb.com/docs/sql/functions/search/full-text#ts_between), [per-node score control](https://serenedb.com/docs/sql/functions/search/scoring#per-node) |
| `ts_highlight` | matched words marked in `search_logs` rows | [Highlighting](https://serenedb.com/docs/sql/functions/search/highlighting#virtual-column) |
| `VACUUM (REFRESH_TABLE)` | publishes the eventually consistent index after the derive and after `create_index` | [VACUUM](https://serenedb.com/docs/sql/statements/vacuum#refreshing--refresh_), [index lifecycle](https://serenedb.com/docs/sql/indexes/inverted#lifecycle) |
| `time_bucket`, gap-filled with `generate_series` | `trend`, the weekly baselines, `latency_stats` buckets | [time_bucket](https://serenedb.com/docs/sql/functions/timestamp#time_bucketbucket_width-timestamp-offset) |
| Window functions (`lag`, `row_number() OVER`, `sum() OVER (PARTITION BY ...)`) | Incident chaining in `sql/derive/30_incidents.sql` and at query time, the top-N plus `(other)` row of `count_by`, the percentile ranks of `top_patterns`, the Pattern dictionary | [Window functions](https://serenedb.com/docs/sql/functions/window_functions) |
| Macros (`CREATE OR REPLACE MACRO`) | the Pattern masks and the Tier 1 template chains, stored in `SLM_SCHEMA` | [CREATE MACRO](https://serenedb.com/docs/sql/statements/create_macro) |
| `regexp_full_match` (RE2) | Tier 1 Pattern assignment, in one SQL step over distinct messages | [regexp_full_match](https://serenedb.com/docs/sql/functions/regular_expressions#regexp_full_matchstring-regex-options) |
| `quantile_cont`, `median` | latency percentiles and the robust z-scores of Phase-Duration Anomalies | [Statistical aggregates](https://serenedb.com/docs/sql/functions/aggregates#statistical-aggregates) |
| Views, LOGIN roles and per-object `GRANT` | access control per Role (`sql/access/roles.sql`) | [CREATE VIEW](https://serenedb.com/docs/sql/statements/create_view) |
| Postgres wire protocol | psycopg 3 on port 7890 | [Run with Docker](https://serenedb.com/docs/installation/docker) |

## Known SereneDB caveats

What we measured on SereneDB 26.10.1 (and 26.09.1.3 where noted) and how the server copes. Details and measurements are in [docs/overnight-decisions.md](docs/overnight-decisions.md), [docs/access-control.md](docs/access-control.md) and issue [#2](https://github.com/OrFrederick/serenedb-log-mcp/issues/2).

- **`statement_timeout` is accepted but not enforced.** The database layer starts a timer and calls `cancel_safe()` after `SLM_QUERY_TIMEOUT_S` (default 15 s); the connection stays usable.
- **`SET default_transaction_read_only` is not enforced.** Every statement runs in an explicit `BEGIN READ ONLY` ... `ROLLBACK`, which SereneDB does enforce (INSERT and CREATE fail with SQLSTATE 25006).
- **Intermittent SIGSEGV.** A BM25 sort with a tie-break key after the score (`ORDER BY BM25(...) DESC, id`) crashed native 26.10.1 in 28 of 70 runs; the single-key sort crashed in 0 of 44. A crash can surface on a later, innocent query. So no SQL template sorts by BM25 plus a second key (ties are unordered), and on a lost connection the database layer waits up to `SLM_RECONNECT_WAIT_S` (30 s) for `SELECT 1`, retries the statement once and adds a note to the result. A second failure becomes an error that suggests `sort='time'`.
- **Digit-hyphen-digit terms fail with XX000** ("Attempted to dereference shared_ptr that is NULL!") when a TSQUERY term on a verbatim-indexed column contains a digit, a hyphen and a digit, as many OpenStack instance UUIDs do. Such filter values fall back to a plain `= ANY(%s::VARCHAR[])` on the same index relation, which returns the same rows and leaves the BM25 scores unchanged.
- **Bound timestamps in `generate_series` fail** with "Invalid unicode (byte sequence mismatch) detected in value construction" under server-side binding, in the table-function form `FROM generate_series(%s::TIMESTAMP, ...)`; `col = ANY(%s)` with a Python list fails too. The templates use `unnest(generate_series(...))`, `unnest(%s::VARCHAR[])` and `= ANY(%s::VARCHAR[])`, which work in both binding modes.
- **A plain SQL filter on the index relation adds to the BM25 score** (+2.0 per filter). Filters are TSQUERY predicates with `::score(NULL)`, so they never change the relevance order.
- **The inverted index is eventually consistent.** The derive step and `create_index` run `VACUUM (REFRESH_TABLE)` after building it.
- **A view over an index relation loses `BM25(e.tableoid)` and the `::score(NULL)` filters**, so each text Role gets an inverted index over its own entry view ([docs/access-control.md](docs/access-control.md)).
- **26.09.1.3, the newest arm64 image**, differs from 26.10.1:
  - `CREATE TEXT SEARCH DICTIONARY ... AS split_text(...)` does not exist; `make derive` reads `version()` and uses the legacy `(TEMPLATE = 'text', ...)` form.
  - A cancel is ignored while a table function such as `generate_series` takes a bound parameter. The server binds on the client on SereneDB older than 26.10, which restores cancellation; `SLM_CLIENT_BINDING=1` forces it on any version.
  - A temporary view over a table cannot be read (InsufficientPrivilege), so the adapter uses temporary tables.
  - 26.10.1 is published for amd64 only and crashes under emulation on Apple Silicon, which is why `make db-up` picks 26.09.1.3 on arm64. The Pattern step has been measured on 26.10.1 only.
- **No row-level security** ("Row-level security is not supported"). Access control per Role uses per-Role views and LOGIN roles instead.

## Limitations

- **The organisers' loader, schema and Ownership Table have not arrived** (T25, #37). Loading uses our dev loader, and Teams come from a stub Ownership Table with fictional Teams (`team_source = 'stub'` on every row); `describe_corpus` and every count grouped by Team carry a note that says so.
- **The worked investigation's Manager answer was drafted from Evidence.** The Engineer and Team Lead answers in [`docs/worked-investigation.md`](docs/worked-investigation.md) are the final messages of real `claude -p` sessions; the Manager session was stopped after its tool calls, so its answer was drafted from its own Evidence (E25 to E28) and checked with `verify_answer`. `make worked-investigation` runs all three Roles again.
- **No eval run with a real model is recorded yet** ([docs/results/eval.md](docs/results/eval.md)).
- **The Incident timeline has not been seen rendering by a human** (#35). Claude Code does not render MCP Apps; it always falls back to text.
- **Incidents are a model, not a ground truth.** The 300 s gap reproduces the published BGL total because it was fitted on it; the per-category counts are the independent check. Precursors (`find_precursors`) and Cascades are always Hypotheses.
- **Time zones.** BGL `ts` is shown as logged, in US Pacific local time; every gap, window, bucket and time filter uses UTC (`epoch_utc`), and a time input with an offset is converted to UTC. OpenStack timestamps carry no zone and are taken as UTC.
- **One Dataset per search, trend and Incident list.** BGL and OpenStack are 12 years apart and their BM25 scores are not comparable, so `search_logs`, `get_context`, `trend`, `list_incidents` and `find_precursors` take one Dataset; `count_by`, `top_patterns` and `describe_corpus` cover both.
- **What the logs cannot say.** Cost, downtime, customer impact and SLA breaches are not in the logs; the Manager's results list them as `not_measured`, `verify_answer` rejects them outside Unknowns, and a root cause stays a Hypothesis unless the Evidence establishes it.
- **The Evidence Ledger is in memory** unless `LEDGER_PATH` points to a file; then it is shared across sessions and server processes, and `export_report.py` and `replay_ledger.py` can read it.
- **Access control is defence in depth only** ([docs/access-control.md](docs/access-control.md)): no row-level security, any login can read server files, and the dev container's trust auth accepts any password.
- **Tier 1 Patterns use the Loghub-2.0 template files**, which are downloaded at setup and never committed. Whether that counts as a "resource listed in the brief" is open; `SLM_PATTERN_TIER1=0` uses the generic masks only.
- **The local stdio route is the documented one.** No hosted endpoint is provided.
- **Not built:** the huge-datasets (remote Parquet), multimodal OTel and demo tracks were dropped.

## Configuration

All settings come from the environment; `uv run serenedb-log-mcp --help` lists them.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `host=localhost port=7890 dbname=postgres user=postgres` | SereneDB connection (libpq string or `postgresql://` URI); also the login `create_index`, `drop_index` and `make access` use |
| `DATABASE_URL_ENGINEER`, `DATABASE_URL_TEAM_LEAD`, `DATABASE_URL_MANAGER` | unset | per-Role connections and the logins `make access` creates; fall back to `DATABASE_URL` |
| `SLM_SCHEMA` | `slm` | schema of the derived tables the tools read |
| `SLM_RAW_SCHEMA` | `public` | schema of the loader's raw tables |
| `SLM_DATA_DIR` | `./data` | downloaded files |
| `SLM_QUERY_TIMEOUT_S` | `15` | client-side query timeout in seconds |
| `SLM_CLIENT_BINDING` | `0` | `1` binds parameters on the client (`psycopg.ClientCursor`) on any SereneDB version |
| `SLM_RECONNECT_WAIT_S` | `30` | how long to wait for SereneDB after a lost connection before the one retry |
| `SLM_MAX_IDLE_CONNECTIONS` | `4` | idle connections kept per pool |
| `LEDGER_PATH` | unset (in memory) | JSONL file of the Evidence Ledger |
| `SLM_EXPORT_DIR` | `./exports` | where `scripts/export_report.py` writes reports |
| `SLM_PATTERN_TIER1` | `1` | `0` derives Patterns without the Loghub-2.0 files |

## Development

```bash
make help         # every target
make check        # lint, typecheck, pure tests (no database); the CI gate
make test-db      # tests against SereneDB at DATABASE_URL; they load the 2k samples into throwaway schemas
make test-corpus  # golden numbers on the full Base Corpus (needs make load-dev and make derive); skips without it
make test-golden  # the same corpus tests, but a missing corpus fails instead of skipping
make docs         # regenerate docs/tools.md after changing a tool
make score-patterns                               # score the Patterns against the Loghub-2.0 labels
make bench-index                                  # rewrite docs/results/index-tradeoff.md
uv run python scripts/ground_truth.py openstack   # rewrite a section of docs/results/ground-truth.md (or: bgl)
```

Tests that need a database skip with the reason when none is reachable; `SLM_REQUIRE_DB=1` turns those skips into failures. CI (`.github/workflows/ci.yml`) runs the pure tests and the L0 contract suite on every pull request. Work is ticket based: see [docs/plan.md](docs/plan.md), the GitHub issues, [GLOSSARY.md](GLOSSARY.md) for the canonical terms and [docs/decisions.md](docs/decisions.md).

## Data and citations

The datasets come from Loghub and are downloaded at setup, never committed. Loghub permits use for research or academic work and asks for these citations:

- Loghub: Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. "Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics." IEEE International Symposium on Software Reliability Engineering (ISSRE), 2023. [arXiv:2008.06448](https://arxiv.org/abs/2008.06448), [github.com/logpai/loghub](https://github.com/logpai/loghub).
- BGL: Adam J. Oliner, Jon Stearley. "What Supercomputers Say: A Study of Five System Logs." IEEE/IFIP International Conference on Dependable Systems and Networks (DSN), 2007, pp. 575-584. [doi:10.1109/DSN.2007.103](https://doi.org/10.1109/DSN.2007.103).
- Loghub-2.0 (template annotations used for Tier 1 Patterns): Zhihan Jiang, Jinyang Liu, Junjie Huang, Yichen Li, Yintong Huo, Jiazhen Gu, Zhuangbin Chen, Jieming Zhu, Michael R. Lyu. "A Large-scale Evaluation for Log Parsing Techniques: How Far Are We?" ACM SIGSOFT International Symposium on Software Testing and Analysis (ISSTA), 2024. [arXiv:2308.10828](https://arxiv.org/abs/2308.10828).
