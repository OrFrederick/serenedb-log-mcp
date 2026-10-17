# Templates vs no templates: can an agent find BGL's most common problem?

Date: 2026-10-17. Dataset: BGL only, 4,747,963 Log Entries (2005-06-03 to 2006-01-04), loaded with the dev loader into SereneDB `serenedb/serenedb:26.09.1.3`.

## Question

Fresh-context Claude agents got one question, **"What's the most common problem in the logs?"**, and nothing else: no repo, no docs, no log files. Only database queries. We compared agents that can see Loghub-2.0 message templates with agents that can't, in the same environment.

## Setup

| Arm | Interface | Tables | Runs |
|---|---|---|---|
| SQL, no templates | `psql` via `docker exec` | `cmp.bgl_entry` | 3 |
| SQL, templates | `psql` via `docker exec` | `slm.bgl_entry` (+ `template_id`), `slm.pattern` (template text) | 3 |
| Postgres MCP, no templates | `@modelcontextprotocol/server-postgres@0.6.2`, headless `claude -p`, only `mcp__postgres__query` allowed | `cmp.bgl_entry` | 3 |
| Postgres MCP, templates | same | `slm.bgl_entry`, `slm.pattern` | 3 |

- **Same columns in both arms.** `cmp.bgl_entry` is `slm.bgl_entry` minus `template_id`, `family_id` and `pattern_key`. So both arms have the same columns (`ts`, `epoch_utc`, `node`, `rack`, `midplane`, `nodecard`, `component`, `level`, `alert_tag`, `message`), and the only difference is the templates.
- **Same prompts in both arms.** The prompts differ only in the table description.
- **Reference answer.** It comes from 2 separate deep-dive agents (59 and 36 queries, about 20 and 13 minutes). Each classified all 465 templates into problem families, attached crash-dump continuation lines to the event that caused them, and counted **episodes**: runs of one family with gaps under 5 minutes. The two agents agree. I re-ran the headline numbers myself.

## Reference answer

| Definition | Most common problem | Evidence |
|---|---|---|
| Failures of any kind, counted as episodes | **Application crashes that write core dumps** (`generating core.N`, `ciod: generated N core files for program …`) | 771 episodes, 126 of 215 days, 33,792 nodes, top node 0.01%; 1,769,273 Log Entries. All INFO, 0 expert-labelled. Mostly user-code crashes. |
| Faults of the machine itself (the definition the deep dives recommend) | **Compute-node kernel crashes** (machine check / data storage interrupt / program interrupt → register dump → `rts panic`) | 500–522 episodes, **170–171 of 215 days**. Leads at 30- and 60-minute gaps, and on every measure in the consistently logged Aug–Jan period. Leading trigger: hardware machine checks, mostly L1 d-cache parity (291 episodes, 142 days). |
| Raw Log Entries among expert-labelled alerts | "data TLB error interrupt" (KERNDTLB) | 152,734 lines, but 99.6% come from **one node** (`R30-M0-N9-C:J16-U01`) in **one 6.4-hour storm** on 2005-06-11. Only about 37 episodes, ranked 7th–9th. |

Core dumps lead at 5-minute gaps but not at 30 minutes or longer. Their logging also has a gap from 2005-07-29 to 2005-08-12. So "kernel crashes" is the safer system-level answer, and core dumps are the most frequent failure overall.

I checked these myself: core-dump family 1,769,235 Log Entries (LIKE match; the template match gives 1,769,273), 33,792 nodes, 771 episodes at a 5-minute gap, 126 days, 0 alert-tagged.

## Results

| Arm | Gave "data TLB error" as the answer | Caught one node = 99.6% | Caught one-day burst | Found `ciod` job-launch family across templates | Named the reference answer | Queries per run |
|---|---|---|---|---|---|---|
| SQL, no templates | 3/3 | **3/3** | 3/3 | 0/3 | 0/3 | 12, 15, 22 |
| SQL, templates | 3/3 | 1/3 | 3/3 | **2/3** | 0/3 | 9, 12, 15 |
| Postgres MCP, no templates | 3/3 | **3/3** | 3/3 | 0/3 | 0/3 | 8–9 |
| Postgres MCP, templates | 3/3 (2 led with "generating core" by line count, then dismissed it as INFO) | 0/3 | 2/3 | **2/3** | 0/3 | 6–10 |

Earlier, uncontrolled run: 3 agents using this project's own MCP tools (`count_by`, `trend`) with Tier 1 Patterns. All 3 said "data TLB error", 0/3 caught the single node, and one called 541 nodes "widespread".

## Findings

1. **No arm found the real answer (0/12).** Every agent counted Log Entries or alert-labelled lines, then hedged by listing nodes and days. None of them counted episodes, and none attached register dumps to the crash that wrote them. The deep dives needed 36–59 queries to do that.
2. **Templates change *what* agents notice, not *whether* they get it right.**
   - **With templates:** agents read the template text, quickly set INFO noise aside, used fewer queries, and saw that one problem is split across many templates (2/3 in each template arm grouped the `ciod` launch failures by prefix). But they trusted the template summary ("541 nodes") and rarely drilled into nodes (1/6 caught the single-node storm).
   - **Without templates:** agents grouped by the expert `alert_tag`, which led them to drill into nodes and days (6/6 caught the storm). But they could only see what the labels cover, and the labels miss core dumps and most launch failures.
3. **The interface doesn't matter much.** `psql` and the Postgres MCP give the same pattern; the templates drive the difference.
4. **The trap that sinks templated agents is "generating core".** It's the largest template (1.7M lines, INFO). Templated agents saw it and dismissed it as "noise" or "a symptom". In fact it's the most frequent failure once you count episodes.

## Why templates didn't help more

- **No parameters.** Loghub-2.0 gives only `LineId, Content, EventId, EventTemplate`, plus `Occurrences` per template. We store `template_id`, the text with `<*>` wildcards, and a count. The values that fill the `<*>` slots are not extracted.
- **The node is a header field, not a template parameter.** Both arms had the `node` column; only the no-template agents used it.
- **No families.** `family_id` is one per template, so 30 `ciod` variants and about 200 register-dump templates stay separate.
- **No episodes.** Nothing turns lines into occurrences. That is the step every agent skipped.

## Implications for the MCP server

- Report each Pattern's concentration: the top source's share and distinct days, not only `distinct_sources`.
- Assign Pattern Families (prefix or verb rules), so that `ciod: Error loading …` variants and register-dump continuations group together.
- Make episode counting a first-class tool or column. This is the Incident model in `docs/decisions.md`, and this experiment shows agents won't build it on their own.
- Don't let INFO level hide failures: `generating core` is INFO.

## Reproduce

```bash
uv run python scripts/fetch_data.py          # BGL + Loghub-2.0 into data/
uv run python scripts/dev_loader.py bgl openstack
uv run python scripts/derive.py --adapter dev   # builds slm.* with Tier 1 Patterns
docker exec serenedb-log-mcp-db psql -U postgres -h localhost -p 7890 -c \
  "CREATE SCHEMA IF NOT EXISTS cmp; CREATE TABLE cmp.bgl_entry AS SELECT entry_id, ts, epoch_utc, node, rack, midplane, nodecard, component, level, alert_tag, message FROM slm.bgl_entry"
```

Postgres MCP config used for the headless runs:

```json
{"mcpServers":{"postgres":{"command":"npx","args":["-y","@modelcontextprotocol/server-postgres@0.6.2","postgresql://postgres@localhost:7890/postgres"]}}}
```

```bash
claude -p --mcp-config pg-mcp.json --strict-mcp-config --allowedTools mcp__postgres__query \
  --disallowedTools Bash Read Grep Glob Edit Write WebFetch WebSearch Agent < prompt.txt
```

Caveats:
- 3 runs per arm is a small sample.
- The Postgres MCP's Node client shifted day buckets to 15:00Z (the agents noticed this).
- The deep dives' family rules are hand-written LIKE rules over template text.
