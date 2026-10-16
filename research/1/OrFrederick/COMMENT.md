## Research: OrFrederick's Claude

**Recommendation:** Define an **Incident** as a chain of matching Log Entries that share a grouping key, where each entry follows the previous one within a gap of `gap_s`. The default key is the alert category or template, merged across all nodes, and the default `gap_s` is 300 s. Compute the Incident with `lag()` plus a running `sum()` in SereneDB at query time, as a parameterised tool. Every Incident carries a **Spread** (distinct nodes, node cards, midplanes and racks) and a **Shape** label (single-node storm, one midplane, multi-midplane, machine-wide).

With these defaults we reproduce Oliner & Stearley's published BGL total of **1,202 filtered alerts exactly**. The 10 categories they list individually all match except APPSEV, where we get 136 and they report 138. The match is exact only for a gap of 300–310 s, so the published counts behave like a 5-minute window, although the paper's text says T = 5 s.

The same query separates the cases the issue asks about:
- One node in a loop: 152,182 lines, which is 43.7% of all BGL alert lines, form 1 Incident on 1 node.
- A machine-wide problem: 136 APPSEV Incidents, 51 of them on at least 64 of the 128 midplanes.
- Chronic trickles: KERNMC has 291 Incidents in 32 of 32 weeks.
- Cascades: KERNTERM is followed by KERNREC on 2,895 nodes, with a median delay of 17 s.

On OpenStack, the 4 labelled instances have no WARNING or ERROR lines, and their line-template profile is identical to that of 162 normal instances. They are found only by timing: a robust z-score on spawn time ranks exactly the 4 labelled instances on top. Query-time grouping takes 0.15–0.2 s over all 348k BGL alert lines and about 1 s over all 4.7M rows, so we do not need a precomputed table.

**Confidence:** High for the BGL definition and its validation, because the exact total reproduces a published ground truth on the real data. High for the OpenStack timing finding (measured). Medium for the ranking and Shape thresholds, which are judgement calls.

### Definitions (proposed glossary additions; all are new terms, none collide with GLOSSARY.md)
- **Burst**: a maximal run of matching Log Entries with the same key on **one node** (or one source) and consecutive gaps ≤ `gap_s`. It answers "is one node looping?"
- **Incident**: a maximal run of matching Log Entries with the same key on **any node**, with consecutive gaps ≤ `gap_s` (default 300 s).
  - This is Oliner's LogFilter: the window is chained and its timestamp updates even when an entry is suppressed.
  - A Log Entry is counted once. The Incident is the derived unit we report as "distinct problems".
  - _Avoid_: alert, event, failure (unless established).
- **Spread**: the distinct count of nodes, node cards (`Rxx-My-Nz`), midplanes (`Rxx-My`) and racks (`Rxx`) touched by an Incident. BGL has 69,252 node strings, 128 midplanes and 64 racks.
- **Shape**: a label derived from Spread and line count, with these values: single-node storm (1 node, ≥ 100 lines), single-node isolated, one midplane, multi-midplane, machine-wide (≥ 64 midplanes).
- **Recurrence**: the number of Incidents of a key and the distinct weeks they fall in. "Chronic" means active in most weeks of the Data Scope. "New" means the first Incident falls inside the analysed period.
- **Cascade**: on the same node, key A is directly followed by key B within `cascade_s` (default 60 s). It is reported as pair counts with distinct nodes and the median delay. It is a co-occurrence Hypothesis, never a cause.
- **Episode** (optional): an Incident with the key dropped, so any category merges. At 300 s this gives 820 Episodes against 1,202 Incidents.

Core SQL (runs as written on SereneDB 26.09; `epoch()`, `lag`, running `sum` and `count(DISTINCT)` are verified):
```sql
WITH a AS (SELECT alert_label, node, rack, midplane, ts, epoch(ts) AS t,
                  CASE WHEN node ~ '^R[0-9A-F]{2}-M[01]-N[0-9A-F]' THEN left(node,10) END AS nodecard
           FROM bgl WHERE alert_label <> '-'),                       -- any filter: FTS hit, level, component
g AS (SELECT *, CASE WHEN t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) <= 300
                     THEN 0 ELSE 1 END AS is_new FROM a),           -- key = alert_label; gap_s = 300
s AS (SELECT *, sum(is_new) OVER (PARTITION BY alert_label ORDER BY t ROWS UNBOUNDED PRECEDING) AS inc FROM g)
SELECT alert_label, inc, min(ts) start_ts, max(t)-min(t) duration_s, count(*) lines,
       count(DISTINCT node) nodes, count(DISTINCT nodecard) nodecards,
       count(DISTINCT midplane) midplanes, count(DISTINCT rack) racks
FROM s GROUP BY alert_label, inc;
```
- A **Burst** uses `PARTITION BY key, node`.
- A per-midplane Incident uses `PARTITION BY key, midplane`.
- A **Cascade** uses `lead(alert_label), lead(t)-t OVER (PARTITION BY node ORDER BY t)`.

### Options compared
| Option | Fit for our use case | Pros | Cons | Evidence |
|---|---|---|---|---|
| Raw line counts | Poor | Trivial | 1 looping node is 44% of all BGL alert lines; the brief explicitly forbids treating lines as incidents | `out_shape.txt` |
| Fixed time buckets (`time_bucket`) | Trends only | Simple; good for Team Lead trend charts | Splits one Incident across boundaries (He et al. 2016 saw this on BGL); counts depend on the grid | He et al. 2016 |
| Burst per (key, node) | Engineer drill-down | Shows looping nodes | Massively overcounts systemic problems: 190,290 Bursts at 300 s, because each compute card logs once | `out_spatial.txt` |
| **Incident per key, spatially merged, chained gap 300 s** | **Best default** | Reproduces the published ground truth (total 1,202); one SQL statement; 0.15 s | One threshold for all categories; can hide an independent same-category failure on another node (Oliner names this weakness) | `out_percat.txt`, `out_sens_fine.txt` |
| Incident per (key, midplane) or (key, rack) | Optional parameter | Keeps distinct hardware regions apart | 18,770 / 10,986 at 300 s, about 15× the published count | `out_spatial.txt` |
| Episode (any key) | Manager overview | Merges correlated categories (820 at 300 s) | Merges unrelated coincidences | `out_episodes.txt` |
| Precomputed Incident table | Not needed | 3–50 ms lookups | Fixed thresholds; stale relative to arbitrary FTS filters | Build 0.28 s; see timings |
| ML (DeepLog, LogAnomaly) | Out of scope | Sequence anomalies | Not explainable as Evidence; heavy | Literature |

### Experiments run
All runs used the shared SereneDB 26.09.1.3 with the dev loader tables. Code and outputs are at https://github.com/OrFrederick/serenedb-log-mcp/tree/research/1-OrFrederick/research/1/OrFrederick (`q.py` runs SQL and reports wall time; `sql_*.sql` hold the queries; `out_*.txt` hold the results). Timings are warm wall times from the client.

**1. Ground truth, BGL.** We compared our counts with Oliner & Stearley's Table 4.

| Category | Raw lines | Oliner filtered | Ours, 5 s | **Ours, 300 s** |
|---|---|---|---|---|
| KERNDTLB | 152,734 | 37 | 86 | **37** |
| KERNSTOR | 63,491 | 8 | 8 | **8** |
| APPSEV | 49,651 | 138 | 227 | **136** |
| KERNMNTF | 31,531 | 105 | 244 | **105** |
| KERNTERM | 23,338 | 99 | 99 | **99** |
| KERNREC | 6,145 | 9 | 9 | **9** |
| APPREAD | 5,983 | 11 | 17 | **11** |
| KERNRTSP | 3,983 | 260 | 290 | **260** |
| APPRES | 2,370 | 13 | 18 | **13** |
| APPUNAV | 2,048 | 3 | 3 | **3** |
| 31 others | 7,186 | 519 | 642 | 521 |
| **Total** | **348,460** | **1,202** | 1,643 | **1,202** |

- The total is 1,202 for a gap of 300–310 s only. Other gaps give 290 s → 1,212, 320 s → 1,201 and 600 s → 1,149.
- The result does not change with `>` or `>=`, or with second or microsecond timestamps.
- A two-stage filter (temporal per node, then spatial) gives the same numbers as the single-stage one at 5 s (`out_oliner_filter.txt`).

**2. Sensitivity** (Incident counts, all 348,460 alert lines; `out_spatial.txt`, 7.6 s for all 25 cells):

| gap | per node | per midplane | per rack | **per category** | any category |
|---|---|---|---|---|---|
| 5 s | 195,894 | 34,131 | 24,618 | 1,643 | 1,491 |
| 60 s | 194,508 | 21,644 | 12,839 | 1,309 | 967 |
| 5 min | 190,290 | 18,770 | 10,986 | **1,202** | 820 |
| 30 min | 168,073 | 15,705 | 8,906 | 1,006 | 614 |
| 1 h | 143,004 | 13,943 | 7,978 | 914 | 532 |

The spatial rule moves the count by about 150×; the gap moves it by about 1.8×. The spatial rule is therefore the decision that matters, and we should expose it as a parameter (`merge_scope = node | midplane | rack | all`).

**3. Shape at 300 s** (`out_shape.txt`):

| Shape | Incidents | Lines |
|---|---|---|
| Single-node storm | 1 | 152,182 |
| Multi-midplane | 226 | 96,113 |
| Machine-wide | 136 | 88,200 |
| One midplane | 119 | 10,740 |
| Single-node isolated | 720 | 1,225 |

**4. Chronic versus new** (`out_chronic.txt`):
- KERNMC: 291 Incidents, 342 lines, active in all 32 of 32 weeks. A chronic trickle.
- KERNDTLB: rank 1 by lines, but 37 Incidents in 8 weeks, and one Incident holds 99.6% of its lines.

**5. Cascades within 60 s on the same node** (`out_cascade.txt`, 0.11 s):

| Pattern A → B | Nodes | Pairs | Median delay |
|---|---|---|---|
| KERNTERM → KERNREC | 2,895 | 4,431 | 17.4 s |
| KERNMC → KERNRTSP | 125 | 160 | 16.8 s |
| APPSEV → KERNMNTF | 100 | 100 | 18.8 s |

**6. OpenStack ground truth** (`out_os_lifecycle.txt`, `out_os_detect.txt`):
- `anomaly_labels.txt` lists **4** UUIDs, so the claim in decisions.md is confirmed.
- They have 0 non-INFO lines. Their template profile (25 lines, 2 "pending task (spawning)" skips) is identical to 162 of the 194 unlabelled instances in the same file. Text search cannot find them.
- Timing can find them. Average spawn time is 38.3 s for the labelled instances (max 50.7 s), against 19.8 s for all others (max 26.7 s). Image creation takes 31.7 s against 13.1 s.
- A robust z-score on spawn time (median/MAD) ranks exactly the 4 labelled instances on top (z = 49.8, 35.4, 18.5, 15.6).
  - At z > 5 we get 5 hits; the 5th is d7d68893 at 26.7 s (z = 11.2).
  - At z > 15 we get 4 of 4 with 0 false positives.
- The labelled instances fall in two clusters: 21:07–21:09 and 21:42–21:47 on 2017-05-14. This matches DeepLog's network throttling "at two different points".
- **API latency did not degrade.** Per-10-minute p95 stayed between 0.368 and 0.408 s, with no 5xx responses. This is an instance-level failure, not latency degradation.

**7. OpenStack latency degradation** (`out_os_latency.txt`):
- The robust z-score of the per-minute API p95 flags **2017-05-17 06:25** (p95 2.24 s, max 4.16 s, z = 66.6).
- It coincides with keystonemiddleware `Bad response code while validating token: 503`, a CRITICAL `Unable to validate token`, and an HTTP 503.
- The same 503 + CRITICAL pair appears at 06:25:02 on 2017-05-16, so it recurs daily at 06:25.

**8. Query time on the full corpus:**

| Operation | Time |
|---|---|
| Incidents over all 348k alert lines | 0.15–0.18 s |
| Incidents over all **4,747,963** rows, keyed by Component | 0.83–1.07 s |
| Ad-hoc filter `content LIKE '%error%'` (427,963 rows) + Incidents | 0.17–0.39 s |
| Materialising `r1_bgl_incidents` | 0.28 s |
| Reading it back | 3–50 ms |

**Recommendation:** run Incident grouping at query time behind a tool with the parameters `filter, key, gap_s, merge_scope`. Optionally cache the default table for the Manager overview.

### Candidate real Incidents (for #5)
**BGL:**
1. **Recurring machine-wide ciod I/O errors (APPSEV)**: 136 Incidents and 49,651 lines from 2005-07-20 to 2006-01-03; 51 Incidents reach 128 of 128 midplanes. The largest was on 2005-08-12 at 23:48 (2,096 lines, 1,024 I/O nodes, 64 racks). This is systemic and our strongest Manager item.
2. **Lustre mount failures (KERNMNTF)**: 105 Incidents and 31,531 lines; 40 are machine-wide. They are concentrated between 2005-08-31 and 2005-09-13. Of these, 100 nodes show an APPSEV → KERNMNTF cascade.
3. **KERNTERM → KERNREC cascade**: on 2,895 nodes. The largest instance was on 2005-11-15 at 19:18: 2,048 nodes on 4 midplanes, KERNTERM followed by KERNREC about 2 min later.
4. **KERNSTOR storm**, 2005-06-13/14: 8 Incidents and 63,491 lines. The largest has 20,480 compute cards on 40 midplanes, over 534 s.
5. **Single-node DTLB storm**: R30-M0-N9-C:J16-U01 on 2005-06-11, from 17:17 for 6.4 h. It produced 152,182 lines, which is 43.7% of all alert lines, from one card. This is our showcase of "lines are not Incidents".
6. **Chronic machine checks (KERNMC → KERNRTSP)**: 291 KERNMC Incidents across all 32 weeks, with the cascade on 125 nodes. A Team Lead recurrence item.

**OpenStack:**
1. **Slow-spawn instances**: the 4 labelled UUIDs, in two clusters on 2017-05-14. They show only in timing.
2. **Daily keystone 503 at 06:25** with an API latency spike (p95 2.24 s) on 2017-05-17, and a 503 on 2017-05-16. **Hypothesis**, not verified: the Debian/Ubuntu default `cron.daily` runs at 06:25 and could restart the identity web server.
3. **Recurring image-cache-manager failure**: `InvalidDiskInfo`, qemu-img on a missing disk. There are 8 occurrences: 1 on 2017-05-16 at 03:19 and 7 in normal2. These sit in the files labelled "normal".
4. Chronic noise, not an Incident to rank: "Unknown base file" WARNING, 3,059 lines, continuous within each file.

### Sources
- Oliner & Stearley, DSN 2007, Table 4 and Algorithm 3.1: https://doi.org/10.1109/DSN.2007.103 ; PDF https://web.archive.org/web/2015/http://adam.oliner.net/files/oliner_dsn_2007.pdf
- Liang et al., DSN 2005 (temporal threshold 2 min): https://doi.org/10.1109/DSN.2005.50 ; DSN 2006 (5 min temporal and spatial thresholds, sensitivity table): https://doi.org/10.1109/DSN.2006.18
- He et al., ISSRE 2016 (fixed, sliding and session windows on BGL): https://jiemingzhu.github.io/pub/slhe_issre2016.pdf
- Du et al., DeepLog, CCS 2017 (OpenStack instance sessions; throttling performance anomalies): https://www.cs.utah.edu/~lifeifei/papers/deeplog.pdf
- Meng et al., LogAnomaly, IJCAI 2019: https://www.ijcai.org/proceedings/2019/0658.pdf ; Le & Zhang, ICSE 2022: https://arxiv.org/pdf/2202.04301
- PagerDuty alert grouping: https://support.pagerduty.com/main/docs/intelligent-alert-grouping , https://support.pagerduty.com/main/docs/content-based-alert-grouping , https://support.pagerduty.com/main/docs/time-based-alert-grouping
- Opsgenie deduplication: https://support.atlassian.com/opsgenie/docs/what-is-alert-de-duplication/
- Datadog correlation: https://docs.datadoghq.com/service_management/events/correlation/patterns
- Splunk ITSI episodes and breaking criteria: https://help.splunk.com/en/splunk-it-service-intelligence/splunk-it-service-intelligence/detect-and-act-on-notable-events/5.0/event-aggregation/configure-episode-filtering-and-breaking-criteria-in-itsi
- Zhao et al., "Understanding and Handling Alert Storm", ICSE-SEIP 2020: https://doi.org/10.1145/3377813.3381363 ; Kleinberg, KDD 2002 (bursts); PELT: https://doi.org/10.1080/01621459.2012.737745
- SereneDB window functions: https://serenedb.com/docs/sql/functions/window_functions ; `time_bucket`: https://serenedb.com/docs/sql/functions/timestamp

Industry practice converges on the same mechanism: a rolling gap window measured from the last grouped alert. PagerDuty uses 5 min as standard and up to 24 h; ITSI's content pack breaks after 8 h; Liang used 2–5 min. This supports 300 s as the default.

### Risks / unknowns
- **Oliner's text says T = 5 s, but the published counts match 300 s.** Liang 2006 used 5 min, so the text may have mixed up seconds and minutes. This is our inference. The APPSEV difference (136 against 138, offset in "others") is unexplained.
- One gap for all categories is crude. Oliner says per-category thresholds would be better. We could expose `gap_s` per call.
- Spatial merging can hide an independent failure of the same category on another node during a storm. The Burst view, keyed per node, is the guard against that.
- BGL alert labels are expert tags. Outside the tagged categories (FTS hits, templates from #6), the key must be a template id, and that depends on the template mining issue.
- The OpenStack results come from very small labelled data (4 instances, 1 file). The 06:25 keystone cause is unverified.
- The dev loader extracted 25 lines per labelled instance, while a raw-file count gives 28. Re-check with the official loader.

### Open questions for the humans
1. Do we adopt "Incident = chained gap 300 s, merged across nodes" as the canonical definition, and add Burst, Incident, Spread, Shape, Recurrence and Cascade to GLOSSARY.md?
2. Should the default key for non-BGL data be the template id (from #6) or the Component?
3. Is the single-node DTLB storm (44% of alert lines from 1 card) or the machine-wide APPSEV recurrence the lead story for the worked investigation in #5?
