## Research: OrFrederick's Claude

**Recommendation:** Use the **"/p/gb1 Lustre mount-failure wave" on BGL (2005-08-31 18:41 to 2005-09-16 20:35, alert category KERNMNTF)** as the worked investigation. Add the **OpenStack slow-VM-boot anomaly** as the second-Dataset thread. Frame both as "workloads fail at start-up". On BGL the I/O nodes cannot mount the parallel filesystem when a block boots. On OpenStack, 4 labelled VMs take 2 to 3 times longer from "Creating image" to "VM Started". Use the **KERNDTLB single-node storm** as a deliberate trap in the Manager answer. By raw line count it is the #1 problem: 44% of all BGL alert lines. But 99.6% of those lines come from one node on one day, so it is one Incident and not a platform risk.

This mix gives every Role a different question and different Evidence:
- Engineer: phrase search, a time window and nearby events.
- Team Lead: episodes per day and spread over all 1,024 I/O nodes.
- Manager: whole-corpus counts by Dataset and Component, and lines compared with episodes.

There is a clear before, during and after. There are two kinds of ground truth: the Oliner & Stearley categories and the OpenStack anomaly labels. The demo also lands the line the judges care about most: "matching Log Entries are not Incidents".

**Confidence:** Medium-high. Every number below comes from queries that ran on SereneDB (`queries.sql`, `results.txt`). The Lustre episode is the cleanest real story in the corpus: one filesystem mount point, all I/O nodes, a sharp start and end. It is **not** a dated incident in the literature, though. Oliner & Stearley only list KERNMNTF ("Lustre mount FAILED") as a software category: 31,531 lines, 105 events after filtering. We know what failed and where, but not why. That is fine as long as we label it.

### Options compared
Episode = alert Log Entries of one category, where a gap of more than 30 minutes starts a new episode (`r5_episodes`). This is a lightweight stand-in until #1 defines Incidents.

| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| **A. /p/gb1 Lustre mount-failure wave (BGL KERNMNTF) + OpenStack slow boot (recommended)** | Best: all 3 Roles, both Datasets, search and aggregation | 26,426 "point /p/gb1" lines in a 17-day window, none before or after. All **1,024/1,024 I/O nodes** affected (`node like '%-I:%'`). 43 episodes in the window, 19 of 57 KERNMNTF episodes machine-wide (60+ racks). Single, searchable phrase. In the window KERNMNTF is 26,722 of 32,851 alert lines (81%). Paper category gives external grounding | Root cause not in the logs: the Lustre server syslogs are not in BGL (Oliner dissertation p.9). The cross-Dataset link is thematic, not causal | Q3, Q4, `r5_episodes` |
| B. KERNDTLB single-node storm 2005-06-11 | Strong as a teaching moment, weak alone | 152,182 lines from **one node** (R30-M0-N9-C:J16-U01) in 6 h 22 m. That is 43.7% of all 348,460 alert lines and 99.6% of KERNDTLB. The paper's filter cuts KERNDTLB to 37 events | One node, one day, no recurrence or Team story, no cause in the logs | Q1, Q2 |
| C. OpenStack slow-spawn anomalies alone | Good ground truth, small | The 4 labelled UUIDs are exactly the 4 slowest spawns: 29.5 to 50.7 s against a normal p95 of about 21 s and a max of 22.56 s. The delay sits only in the image→VM Started phase (22.4 to 44.5 s against a normal max of 15.3 s). The abnormal file has **0 ERROR lines**, so text search cannot find them | Only 4 instances in 2 h 17 m. Engineer "search" is a UUID lookup, not a phrase. No Team Lead recurrence | Q6, Q7 |
| D. KERNSTOR partition crash 2005-06-13/14 | Dramatic, but it is a cascade with an unclear cause | 63,491 lines on 22,529 nodes across 22 racks, in 5 episodes. Each is followed by register-dump cascades (47,105 "instruction address" lines) | Probably one job or partition dumping core on every node. Hard to tell a story; no recurrence after June (except small November episodes) | episode table, 2005-06-13 17:00 to 06-14 11:00 |
| E. APPSEV ciod "Link has been severed", chronic | Best pure Team Lead recurrence | 49,651 lines in 130 episodes over 67 days on all 1,024 I/O nodes. The paper keeps 138 events after filtering, the most of any big category | No before or after, no single drill-down target, weak for the Engineer | Q1 |

### Experiments run
Branch: https://github.com/OrFrederick/serenedb-log-mcp/tree/research/5-OrFrederick/research/5/OrFrederick (`queries.sql` holds the golden SQL, `results.txt` holds the outputs). Everything ran on SereneDB 26.09.1.3. Every query took under 1 s, and most took under 0.1 s.

**BGL profile**
- Levels: INFO 3,735,813, FATAL 855,195, ERROR 112,355, WARNING 23,357, SEVERE 19,213, FAILURE 1,714.
- Components: KERNEL 4,324,651, APP 228,536, DISCOVERY 97,172, MMCS 88,930, then a long tail.
- 41 alert categories, 348,460 alert lines, which matches Oliner & Stearley exactly. FATAL (855k) is not the same as alert (348k), so severity alone over-counts.
- Weekly alert lines peak at 152,349 in the week of 06-06 (the KERNDTLB storm) and 68,792 in the week of 06-13 (KERNSTOR). After that they sit between 0.1k and 17k per week, with an Aug–Sep hump: 10,091, 6,248, 17,110, 11,018 and 6,451 in the weeks from 08-15 to 09-12.
- Episode grouping turns 348,460 alert lines into **1,006 episodes**.

**Ranking flip (Q1)**

| Category | Lines | Episodes | Days | Nodes | Top-node share |
|---|---|---|---|---|---|
| KERNDTLB | 152,734 | 22 | 11 | 541 | 99.6% |
| KERNSTOR | 63,491 | 5 | 5 | 22,529 | 0.0% |
| APPSEV | 49,651 | 130 | 67 | 1,024 | 0.1% |
| KERNMNTF | 31,531 | 57 | 25 | 1,024 | 0.2% |
| KERNTERM | 23,338 | 44 | 28 | 9,524 | 0.1% |
| KERNRTSP | 3,983 | 236 | 124 | 3,394 | 0.6% |

**Lustre wave (Q3–Q5)**
- KERNMNTF lines by phase: 702 before 08-31 18:41 (3 days, "block_id" variant only), 26,722 in the window (12 days with failures), 4,107 after.
- The "point /p/gb1" text appears **only** in the window.
- Machine-wide bursts: for example 2005-09-02 12:39:01, when 1,024 lines from 1,024 I/O nodes arrived within 2.5 minutes, so one boot event produced 1,024 Log Entries. Daily KERNMNTF lines: 3,081 (08-31), 1,093, 3,344, 4,352, 2,819, 1,860, 3,582, 0 (09-07), 2,960, then 0 for 09-09 to 09-12, 2,347 (09-13), 421 (09-15), 859 (09-16).
- From 09-10 to 09-13, APPSEV ciod "link severed" errors rise: 1,024, 942, 1,311 and 1,118 per day. This is a Hypothesis about a related I/O-path problem, not established.
- The line just before the first failure is "job 35132 timed out. Block freed." at 18:40:39, 64 s earlier.
- The BGL log is sparse around these events: 2 lines in 10 minutes. "Nearby events" therefore needs a window of minutes to hours, not seconds.

**OpenStack (Q6, Q7)**
- 3 files. The abnormal file covers 2017-05-14 19:39 to 21:56 with 198 instances. The normal files hold 557 and 1,315 instances.
- `anomaly_labels.txt` lists **exactly 4 UUIDs**.
- Image→VM Started, mean: about 13.1 s for every unlabelled group, 31.7 s for the labelled 4.
- Only 5 instances in the whole corpus exceed the normal max of 15.3 s: the 4 labelled ones and **d7d68893-c44f-4e7c-8d4b-0e7801d62776** at 19.8 s, which is unlabelled. This is an extra outlier our tool would flag.
- Each labelled instance has 25 lines, all INFO, the same template sequence as normal instances.
- All 186 ERROR and 2 CRITICAL lines are in the *normal* files: qemu-img InvalidDiskInfo tracebacks in the image cache periodic task.
- **Verdict on the decisions.md claim: confirmed.** There are 4 UUIDs. They are visible in timing and not in message text.

**Full-text search**
- The default `USING inverted (line_no, content)` index without a dictionary is verbatim: `@@ 'lustre'` returned 0.
- The new expression syntax (`CREATE TEXT SEARCH DICTIONARY x AS split_text(...)`) from the current docs is a **syntax error on 26.09.1.3**. It failed both before and after the shared-config incident on 2026-10-16. It is a parser error, so the incident is probably not the cause, but someone should check it again after the container restarts. Toolset/FTS issue owners should note this.
- A sequential scan `content ilike '%lustre mount failed%'` over all 4.7M rows takes 0.24 s, and the prefix `like` takes 0.05 s. The index is not required for correctness, but the Engineer story should use the real FTS tool once #2 settles the dictionary syntax.
- Scratch slice table and index are dropped.

### Draft storyline (top pick)
Team names below are placeholders until the organisers' Ownership Table arrives.

**Manager:** "Across the whole platform, which observed problems deserve attention first?"
- Tool calls:
  - `aggregate_counts(dataset=all, group_by=dataset, component)` over the whole Base Corpus.
  - `aggregate_counts(bgl, group_by=alert_category, metrics=[lines, episodes, nodes, days])`.
  - `aggregate_counts(openstack, group_by=source_file, level)`.
- Evidence:
  - BGL is 4,747,963 Log Entries and OpenStack is 207,820.
  - KERNDTLB is the #1 category by lines (152,734) but is 1 node on 1 day (99.6%).
  - KERNMNTF: 57 episodes, all 1,024 I/O nodes, 19 machine-wide.
  - APPSEV: 130 episodes over 67 days.
  - The OpenStack abnormal window has 0 ERROR lines.
- Answer: priority 1 is filesystem mount at block boot (storage path), with the KERNDTLB storm explicitly demoted ("one noisy node, not a platform risk"). VM boot latency is a second item to watch.
- No downtime or cost claims.

**Team Lead:** "Is the Lustre mount failure recurring, since when, and who owns it?"
- Tool calls:
  - `trend(bgl, filter=category KERNMNTF, bucket=day, 2005-08-01..2005-10-01)`.
  - `aggregate_counts(group_by=team)`.
  - The episode/spread tool.
- Evidence:
  - Daily series: 0 before 08-31, then 1k–4.4k per day on 9 of 12 days, last on 09-16. Afterwards only sporadic "block_id" variants: 4,107 lines over 10 days to 2006-01-03.
  - 43 episodes in the window, 1,024/1,024 I/O nodes.
  - Rising APPSEV in the same weeks (Hypothesis).
- Next Step: assign to the storage/filesystem Team and investigate it before the ciod (APPSEV) recurrence.

**Engineer:** "What failed on 2005-09-02 around 12:39, and what happened just before and after?"
- Tool calls:
  - `search_logs(dataset=bgl, phrase="Lustre mount FAILED", from=2005-09-02 12:00, to=13:00)`. This returns 1,024 matches and is Truncated to 20 rows.
  - `nearby_events(line, ±30 min)`.
  - For OpenStack: `search_logs(instance_id=a445709b…)` and a latency/phase aggregation, which returns 44.45 s against a normal max of 15.3 s.
- Evidence: every I/O node bglio0..N reports "point /p/gb1" within 2.5 minutes, followed by torus/tree "critical input interrupt" warnings and a LINKIAP iap failure at 12:55.
- Next Step: check Lustre server and MDS logs for /p/gb1 for 08-31 to 09-16. These are not in the Base Corpus.

**What the data establishes:** which mount point, which nodes, when, how often, and that the problem stopped after 09-16. **What it does not establish:** the root cause (no Lustre server logs), job impact, or whether the 08-31 to 09-16 change was a configuration or a hardware change.

### Sources
- Oliner & Stearley, "What Supercomputers Say", DSN 2007. Read via Oliner's 2009 dissertation, ch. 2 (https://theory.stanford.edu/~aiken/publications/theses/oliner.pdf):
  - BGL has 348,460 alerts in 41 categories, 1,202 after filtering.
  - KERNMNTF 31,531 → 105, KERNDTLB 152,734 → 37, APPSEV 49,651 → 138.
  - Lustre and disk syslogs are not in the log.
  - FATAL/FAILURE as an alert proxy gives 59% false positives.
  - Ch. 5.4.1 found "ddr: Unable to steer" (243 lines) as an untagged actionable message. Our data has 243 such lines on 125 nodes, all labelled '-'.
- Du et al., DeepLog, CCS 2017, §5.1.2 (https://www.cs.utah.edu/~lifeifei/papers/deeplog.pdf): OpenStack fault injection and a "Took * seconds to build instance" anomaly signal.
- Loghub READMEs (https://github.com/logpai/loghub/tree/master/BGL, …/OpenStack) and Zhu et al., ISSRE 2023 (https://arxiv.org/abs/2008.06448), with their citation requirements.
- SereneDB docs: CREATE TEXT SEARCH DICTIONARY (https://serenedb.com/docs/sql/statements/create_text_search_dictionary), ts_phrase / phraseto_tsquery.

### Risks / unknowns
- The episode grouping is my own 30-minute gap rule per category. #1 may define Incidents differently, and the numbers will shift. The 30 s, 5 min and per-node variants were not tested.
- The cause of the Lustre wave is Unknown. The literature has no dated account of it.
- The OpenStack link is thematic ("start-up path"), not causal. The Datasets are from different years and systems. We must say so.
- Rack/midplane parsing: KERNPOW and MASABNORM have non-node sources (rack is null). Distinct rack counts are 64 (R00–R77).
- The Ownership Table is not supplied yet, so Team names in the story are placeholders.
- The FTS dictionary syntax on our SereneDB version is unresolved. This affects the Engineer phrase search.

### Open questions for the humans
1. Is a thematic two-Dataset story ("start-up path failures") enough for minimum requirement 2, or do we want the Manager whole-corpus aggregation alone to carry OpenStack?
2. Should the KERNDTLB "trap" be shown on stage? It costs about 30 s but demonstrates "Log Entries are not Incidents".
3. Do we flag the unlabelled outlier d7d68893 (19.8 s) in the demo as "consistent with an unlabelled anomaly" (a Hypothesis)?
