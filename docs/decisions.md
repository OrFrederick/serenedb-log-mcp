# Decisions and known facts

This is a living record from the design grilling. **Settled** items are agreed by the team. **Open** items wait for a research issue.

## Settled
- **Name**: `serenedb-log-mcp`.
- **Team**: 3 people. Each person drives their own Claude agents. Time and tokens are not the constraint. The goal is to win, so we do every award track and no half solutions.
- **Stack**: Python with the official MCP SDK, psycopg 3 to SereneDB, `uv` for setup and pytest for tests.
- **Interface**: the MCP server is the product, and Claude (Desktop or Code) is the agent. The Roles are exposed as MCP prompts. There is no custom agent app.
- **Evidence Ledger**: every tool call returns an evidence id, the executed SQL, the parameters, the Data Scope, the total match count, the returned count, a `truncated` flag and the query time. Every call is stored in a ledger. Answers cite `[E<n>]` next to each number. Every answer is split into Observed Facts, Hypotheses, Unknowns and Next Step.
- **Verification**: golden SQL for every worked-investigation claim, enforced by pytest. A replay script re-runs ledger entries and checks the numbers in the final answers.
- **Local dev**: run SereneDB in Docker (`serenedb/serenedb`, port 7890). Point `DATABASE_URL` at either the local instance or the provided instance.
- **Organiser materials**: the official loader, schema and Ownership Table are supplied by the organisers and are the source of truth. We have not received them yet. Until they arrive, a throwaway dev loader unblocks work, and we replace it when the official one lands.
- **Award order**: core requirements first. Best MCP Server is part of the core. Then, in order: agent-managed index with benchmark, huge datasets, multimodal correlation, access control per Role.
- **Multimodal source**: `otel_demo/` in the public-pme bucket, which holds logs, traces and metrics joined on TraceId and stored as remote Parquet. Use Nezha only if we need ground-truth faults.
- **Huge datasets**: query `text_bench/part_000` (1B rows) in place and never index the whole set. The agent builds an index over a time slice on demand. Report rows, wall time and the trade-off between index build time and time saved.
- **Access control per Role**: optional and informal in the brief. SereneDB has no RLS ("Row-level security is not supported"). If we do this track, we use filtered views per Role, a LOGIN role per Role, and GRANT on the views only. The README states the limitation.
- **Log Entries are not incidents**: count entries and derived incidents separately. The incident model is open research, see below.

## Open (research issues)
- Template mining and log parsing: which tool or approach to use.
- Incident model: how to get from Log Entries to Incidents. Examples: one node spamming versus many nodes, bursts, cascades.
- Final MCP tool set: names, schemas and limits.
- Tool-selection eval harness.
- How to make Role answers differ in substance.

## Known facts (verified by research agents, 2026-10-16)
- SereneDB speaks the Postgres wire protocol on port 7890, and psycopg 2 and 3 are supported. Example connection: `psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres")`.
- Full-text search uses an inverted index that you query as its own relation: `FROM idx WHERE col @@ ...`. It supports BM25 (`BM25(idx.tableoid)`), `ts_phrase`, proximity `##`, `ts_levenshtein`, `ts_ngram`, `ts_regexp` and `ts_highlight`. The index is eventually consistent, so run `VACUUM (REFRESH_TABLE) t` after loading. Phrase queries need the `position` dictionary option.
- SereneDB has `time_bucket`, `date_trunc`, `epoch` and `make_timestamp(_ms)`, and full window functions. It has **no** `to_timestamp`.
- Remote Parquet: `read_parquet('https://...')` works. Pass a list of URLs, because HTTPS globs are unlikely to work. `EXPLAIN ANALYZE` reports rows and wall time but not bytes scanned. An inverted index over a view of external data is a snapshot, and you refresh it with `REINDEX`.
- `text_bench`: 52 files of about 1B rows each, about 2 TB in total. `otel_demo/`: logs (91.6M rows), traces (216.7M rows) and metrics.
- Loghub-2.0 (Zenodo 8275861) ships full template annotations: BGL has 4,631,261 lines and 320 templates, OpenStack has 207,632 lines and 48 templates. Its Content field holds only the message, and its line counts differ from the raw corpus.
- OpenStack anomalies: reportedly about 4 instance UUIDs with injected faults, visible in timing data but not in message text. Not yet verified.
