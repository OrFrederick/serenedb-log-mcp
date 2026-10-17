# Demo v1 run sheet (T24)

A demo of the core in 5 minutes or less. The order follows `docs/decisions.md`, Differentiator 9: the line-versus-Incident reveal, live self-verification, the ground-truth score, then the refusal. The beat for visuals in Claude (Differentiator 5) comes from T23 and is added in T29, together with the award-track results. The scale result goes on a slide, not into a live wait.

`docs/demo/beats.yaml` holds the same beats in a form the scripts can run. Every prompt below is in that file verbatim, and `tests/unit/test_demo_beats.py` keeps the two in step. The numbers on the slides come from `docs/results/` and `docs/worked-investigation.md` only (`docs/demo/slides.md`).

## Before the recording

1. **Use the full Base Corpus.** Point `DATABASE_URL` at SereneDB with the Base Corpus loaded and derived (`make load-dev`, `make derive`), and set `SLM_SCHEMA` if the derived tables are not in `slm`. Wait at least a minute after any index rebuild (`make derive`), because results right after a rebuild were briefly wrong (#36).
2. **Raise the query timeout.** Under concurrent load, two whole-BGL `alert_categories` searches ran past the default 15 s `SLM_QUERY_TIMEOUT_S` and answered at 90 s (#36). The demo runs with 60 s: `docs/demo/mcp.demo.json` sets it for Claude, and `scripts/demo_record.py` uses it unless the environment sets another value.
3. **Warm the caches and time the tool side.** Run `make demo-record` twice. The first run warms SereneDB. The second run prints the time of every call against the beat's slot, and leaves the ledger for the replay in `exports/demo-ledger.jsonl`.
4. **Check the fallback.** `make demo-replay` must print every beat with its Evidence and no "not in the ledger" line. It reads only the ledger and needs no SereneDB.
5. **Start Claude with the demo configuration**, from the repository root:
   ```
   rm -f exports/demo-live-ledger.jsonl
   claude --mcp-config docs/demo/mcp.demo.json --strict-mcp-config
   ```
   The live session writes its Evidence to `exports/demo-live-ledger.jsonl`, so `get_evidence`, `verify_answer` and `export_investigation` see one investigation, and the session can be replayed afterwards.
6. Have the self-verification draft (beat 4) ready to paste. Fill it in after the warm-up run, with the numbers of that run (see beat 4).

## Run sheet

| Time | Beat | Role | Tools the agent should call | If the live run stalls |
|---|---|---|---|---|
| 0:00 to 0:15 | 1. Opening | none | none | |
| 0:15 to 1:15 | 2. Lines versus Incidents | Team Lead | `count_by` by alert category, by node; `trend` per day by node | `make demo-replay REPLAY_ARGS="--beat 2"` |
| 1:15 to 1:50 | 3. The Engineer's view of the same lines | Engineer | `search_logs` (full text), `get_context` | `make demo-replay REPLAY_ARGS="--beat 3"` |
| 1:50 to 2:50 | 4. Live self-verification | Team Lead | `verify_answer` twice | `demo_replay.py ... --draft <draft> --role team_lead` |
| 2:50 to 3:45 | 5. The ground-truth score | Engineer | `latency_stats` (spawn per instance), `count_by` by level | `make demo-replay REPLAY_ARGS="--beat 5"` |
| 3:45 to 4:30 | 6. The refusal | Manager | `count_by` by Team, `count_by` by Dataset | `make demo-replay REPLAY_ARGS="--beat 6"` |
| 4:30 to 4:55 | 7. Show your work | Engineer | `export_investigation` | open `docs/worked-investigation.md` |

The slots add up to 295 s, which leaves 5 s of slack.

## Beats

### 1. Opening (15 s)

Prompt: (no prompt: the presenter says what the server is and who the three Roles are)

Say: "Logs answer different questions for different people. SereneDB does the searching and the counting, and Claude explains the Evidence it gets back. We ask one investigation as the Team Lead, the Engineer and the Manager, over BGL and OpenStack."

### 2. Lines versus Incidents (60 s)

Prompt:

> I'm the Team Lead. Over the whole BGL period, which alert category is the loudest, and is that many problems or one? Break it down by node and by day.

Expected Evidence:
- `count_by(group_by='alert_category', dataset='bgl', top=5)`: KERNDTLB is the largest alert category. The (non-alert) row is larger, and the answer should say what it is.
- `count_by(group_by='node', alert_categories=['KERNDTLB'], top=5)`: one node holds nearly all of the category's Log Entries.
- `trend(alert_categories=['KERNDTLB'], bucket='day', group_by='node', top=3)`: the peak bucket of that node is a single day.

Say: "The biggest count in the system is one node on one day, not a fleet-wide problem. Every tool says Log Entries, never Incidents."

The exact numbers come from the recorded ledger and from `docs/worked-investigation.md` (T18). Once `list_incidents` (T27) is merged, this beat adds `list_incidents(dataset='bgl', pattern_key='KERNDTLB', sort='entries')`, so the Incident count and its Spread stand next to the Log Entry count.

### 3. The Engineer's view of the same lines (35 s)

Prompt:

> I'm the Engineer on call. Show me the data TLB error lines on R30-M0-N9-C:J16-U01 and what that node logged just before and after the first one.

Expected Evidence:
- `search_logs(dataset='bgl', query='"data TLB error interrupt"', sources=['R30-M0-N9-C:J16-U01'], limit=5)`: a full-text search that returns a sample of the lines. `truncated` is true, and total_matches gives the exact count.
- `get_context(entry_id=<first hit>, before=10, after=10)`: the node's Log Entries around the first hit, with `offset_s`.

Say: "The Engineer gets raw lines and their neighbours. The Team Lead got at most three sample lines. The Manager gets none."

### 4. Live self-verification (60 s)

Prompt, followed by the pasted draft:

> Check this draft against the Evidence before I send it to my team.

The draft is the Team Lead answer from beat 2 with one count rounded the way people round in chat ("about Nk"). Write it after the warm-up run, with the Evidence ids of the live session:

```
## Observed Facts
- KERNDTLB is the loudest alert category with about <its count rounded to thousands>k Log Entries [E1].
- Almost all of them come from one node, R30-M0-N9-C:J16-U01, on one day [E2] [E3].

## Hypotheses
- One looping node explains the category, not a fleet-wide problem.

## Unknowns
- Whether the node was repaired: these logs hold no maintenance records.

## Next Step
- Ask the Team that owns KERNEL to check R30-M0-N9-C:J16-U01 first.
```

Expected Evidence:
- `verify_answer(role='team_lead', draft=...)` returns `ok: false` with one `rounded_number` problem. The hint names the exact value in the cited Evidence.
- Claude replaces the rounded count with the exact one and calls `verify_answer` again, which returns `ok: true`.

Say: "Claude checks its own draft. Every number has to be in the Evidence it cites, exactly as the database returned it."

Offline fallback: `uv run python scripts/demo_replay.py exports/demo-ledger.jsonl --draft <draft.md> --role team_lead` runs the same checks on the recorded ledger. The draft has to cite that ledger's ids, which are E1 to E3 for beat 2.

### 5. The ground-truth score (55 s)

Prompt:

> I'm the Engineer. Which OpenStack instances took unusually long to spawn, and does any error line point at them?

Expected Evidence:
- `latency_stats(metric='spawn', group_by='instance', limit=6)`: the flagged instances, slowest first, with robust z-scores.
- `count_by(group_by='level', dataset='openstack', sources=['a445709b-6ad0-40ec-8860-bec60b6ca0c2'])`: the slowest labelled instance has INFO lines only.

Say: "Timing finds the labelled anomalies, and text search cannot find them." Then switch to the ground-truth slide (`docs/results/ground-truth.md`). The score comes from that file, not from the live run, because the labels are read only by `scripts/ground_truth.py`.

### 6. The refusal (45 s)

Prompt:

> I'm the Manager. What did the KERNDTLB problem cost us, how long were we down, and what should we fix first?

Expected Evidence:
- `count_by(group_by='team', alert_categories=['KERNDTLB'])`: the measured frequency and the owning Team.
- `count_by(group_by='dataset')`: the whole Base Corpus, so the Manager sees both Datasets in one aggregation.
- The answer has no cost, downtime or customer figure. Unknowns says the logs do not measure them, and the Next Step is a prioritised action that rests on frequency and the affected Component. `verify_answer(role='manager')` returns ok.

Say: "The Manager gets a priority, not a made-up euro figure. What the logs cannot establish is stated as unknown."

### 7. Show your work (25 s)

Prompt:

> Export this investigation as a report.

Expected Evidence: `export_investigation(role='engineer', ...)` returns the Markdown report with every tool call, its SQL and result, and each answer with its `verify_answer` check. `scripts/replay_ledger.py` re-runs the SQL of the ledger and checks that the numbers still hold.

Say: "Every number in this demo can be traced to a query that ran, and the query can be run again."

## Timing

The timed dry run of the tool side uses `make demo-record`. The PR that changes this run sheet states the timing it measured.

A live run with Claude adds Claude's reasoning time. That run uses the full Base Corpus and the final answers, so it happens after T18 and is recorded in the T18 or T29 PR, together with the backup video, which only a human can record.
