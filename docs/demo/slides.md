# Demo v1 slides (T24)

The outline of the slides for the 5-minute demo (`docs/demo/script.md`). A human designs the final deck. T29 adds the award-track results (agent-managed index, huge datasets, multimodal correlation, access control per Role, visuals).

Rule for every slide: a number appears only with the file it comes from, either a file in `docs/results/` or `docs/worked-investigation.md`, and it is quoted exactly as written there. `tests/unit/test_demo_slides.py` checks that each number on a slide line is in the file that line cites. A slide that waits for `docs/worked-investigation.md` (T18) says so and shows no number yet.

## Slide 1: The right insight for the right person

- One question gets three answers: the Engineer gets lines and neighbours, the Team Lead gets counts and trends per Component and Team, and the Manager gets a priority from measured frequency.
- SereneDB searches and counts. Claude explains the Evidence that comes back, and every claim cites the query that produced it.

## Slide 2: How it works

- An MCP server with read-only, parameterised tools: search (`search_logs`, `get_context`), aggregation (`count_by`, `trend`, `latency_stats`), and the self-check and report tools (`verify_answer`, `export_investigation`).
- Every result is an Evidence record: query, bound parameters, Data Scope, total_matches, truncated and query time, kept in the Evidence Ledger as `[E<n>]`.
- Answers have four sections: Observed Facts, Hypotheses, Unknowns and Next Step.

## Slide 3: Log Entries are not Incidents

- Headline numbers of the reveal: from `docs/worked-investigation.md` (T18). Pending: no number on this slide until T18 lands.
- The message: the loudest alert category is one node on one day. Counting lines without that context sends a Team the wrong way.

## Slide 4: Claude checks its own answer

- `verify_answer` checks every number and every `[E<n>]` against the Evidence Ledger before Claude answers. It rejects rounded numbers, ranges, unmarked sums and citations that do not exist.
- The worked example of the self-verification beat comes from `docs/worked-investigation.md` (T18). Pending until then.

## Slide 5: Scored against ground truth (OpenStack)

- Slow spawns found by timing alone: 4 of 4 labelled instances flagged, ranked 1, 2, 3, 4 by spawn time (docs/results/ground-truth.md).
- At the default min_z 5, 5 instances are flagged, a precision of 4/5 (docs/results/ground-truth.md).
- The robust z-score runs over 2,066 instances with a median of 19.74 s and a MAD of 0.42 s (docs/results/ground-truth.md).
- Text search finds none of them: each labelled instance has 28 Log Entries, and 0 of them are above INFO (docs/results/ground-truth.md).
- The fifth flagged instance is not labelled. It is a Hypothesis, not a hit: its spawn_z is 11.2 (docs/results/ground-truth.md).
- latency_stats(metric='spawn') took 312 ms in SereneDB (docs/results/ground-truth.md).
- BGL ground truth against Oliner and Stearley: added when the BGL section of `docs/results/ground-truth.md` lands (T10, PR #86).

## Slide 6: Patterns inside the database

- Every Log Entry gets a Pattern in one SQL derive step. Agreement with the Loghub-2.0 labels is 99.99991% on BGL and 100% on OpenStack (docs/results/patterns.md).
- BGL: 465 Patterns over 4,747,963 Log Entries. OpenStack: 73 Patterns over 207,820 Log Entries (docs/results/patterns.md).
- The whole derive takes 14.3 s, of which the Pattern step takes 5.4 s (docs/results/patterns.md).

## Slide 7: The Manager gets a priority, not an invented cost

- The Manager's tools return totals and facets, never raw Log Entries.
- Cost, downtime and customer impact are not in these logs. The answer says so under Unknowns, and `verify_answer` rejects a figure for them.
- The answer still ends with a prioritised action that rests on measured frequency and the affected Component.

## Slide 8: Show your work

- `export_investigation` turns the Evidence Ledger and the answers into the worked investigation: every question, tool call, SQL statement, result and answer, with what the data does and does not establish.
- `scripts/replay_ledger.py` re-runs the SQL of a ledger and checks that every number still holds. `scripts/demo_replay.py` shows a recorded demo offline.

## Slide 9: Limits

- Teams come from a stub Ownership Table until the organisers' table replaces it (T25).
- Counts are Log Entries. An Incident needs `list_incidents` (T27), and the demo labels which of the two it shows.
- A timing flag is an Observed Fact about timing. Any cause behind it is a Hypothesis, because the flagged Log Entries carry no error.
