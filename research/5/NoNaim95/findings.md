# #5 findings: worked investigation (NoNaim95's Claude, profiler subagent report, saved by parent)

The golden queries are in r5_golden.py. Its output is in out/r5_golden_run.txt (15 queries, 3 runs each, median reported).

## BGL profile
- 4,747,963 Log Entries.
- Levels: INFO 3,735,813; FATAL 855,195; ERROR 112,355; WARNING 23,357; SEVERE 19,213; FAILURE 1,714.
- 316 rows have junk levels: the dev loader shifts columns when the node field is `-`.
- FATAL does not mean alert: KERNEL FATAL has 626,450 entries but only 284,344 carry an alert tag. O&S Table 5 reports a 59.34% false-positive rate for FATAL/FAILURE lines.

### Alert categories (episode = 30-minute gap per tag)

| tag | entries | episodes | nodes | days | midplanes | top-node share |
|---|---|---|---|---|---|---|
| KERNDTLB | 152,734 | 22 | 541 | 11 | 44 | 99.6% |
| KERNSTOR | 63,491 | 5 | 22,529 | 5 | 45 | ~0 |
| APPSEV | 49,651 | 130 | 1,024 | 67 | 128 | 0.1% |
| KERNMNTF | 31,531 | 57 | 1,024 | 25 | 128 | 0.2% |
| KERNTERM | 23,338 | 44 | 9,524 | 28 | 128 | 0.1% |
| KERNREC | 6,145 | 9 | 4,609 | 7 | 10 | ~0 |
| KERNRTSP | 3,983 | 236 | 3,394 | 124 | 103 | 0.6% |

### Top node
- R30-M0-N9-C:J16-U01 has 152,329 lines, 152,185 of them alerts. That is 43.67% of all BGL alerts.
- Its midplane, R30-M0, has 153,171 alerts. The next midplane, R63-M0, has 4,830.

### Biggest days and bursts are not alert days
| Date | Lines | Alerts | What it is |
|---|---|---|---|
| 07-09 | 381,827 | 990 | "generating core" on 32,768 nodes |
| 12-01 | 271,341 | — | rbs statistics |

- The biggest minute was 2005-09-20 12:06, at 16,409 lines/min: idoproxydb ASSERT plus Ido packet timeout from the NULL node.
- None of the top 20 minutes contain alerts.

### O&S 5 s per-category filter
Our counts, with O&S Table 4 in brackets.
- Exact matches: KERNSTOR 8 [8], KERNTERM 99 [99], KERNREC 9 [9], APPUNAV 3 [3].
- Mismatches: KERNDTLB 86 [37], APPSEV 227 [138], KERNMNTF 244 [105].
- Total: 1,643 [1,202].
- Likely cause of the gap: O&S filter across any source, while we chain only on the previous line of the same tag.

## OpenStack profile
- All three files come from one compute host and one nova-compute pid.

| File | Entries | ERROR+CRIT | WARN |
|---|---|---|---|
| abnormal (05-14 19:39–21:56) | 18,434 | 0 | 275 |
| normal1 | 52,312 | 25 | 855 |
| normal2 | 137,074 | 163 | 1,988 |

### API latency is flat across files
| Endpoint | p50 | p95 | p99 |
|---|---|---|---|
| GET servers/detail | 0.265 | 0.365 | 0.436 |
| POST servers | 0.504 | 0.648 | 0.765 |

POST servers p50 is 0.507 in the abnormal file, against 0.504/0.503 in the normal ones.

### The 4 labelled anomalies
- They have the same 25-line template set as 1,775 normal instances, with no ERROR or WARN lines.
- They are the 4 slowest spawns in the corpus: 50.72, 41.81, 31.27 and 29.46 s. Normal p50 is 19.7 s and normal max is 22.56 s.
- Rank #5 is the unlabelled d7d68893 at 26.69 s.
- The delay sits between "Creating image" and "VM Started": p50 29.93 s for the anomalies against 13.07 s for normal instances.
- They come in two pairs, at 21:07–21:09 and 21:42–21:47.
- DeepLog §5.2 says it throttled the network "at two different points". Linking that to these labels is our inference; no source states it.

## Literature
- **O&S DSN 2007**
  - 4,747,963 lines, 348,460 alerts and 41 categories, all matching our counts.
  - Table 4: KERNDTLB 152,734→37, KERNSTOR 63,491→8, APPSEV 49,651→138, KERNMNTF 31,531→105, KERNTERM 23,338→99, KERNRTSP 3,983→260.
  - It names no storms with dates.
- **Taerat ARES 2009**
  - June 13–14 2005 peak.
  - 2005-11-07 Temperature Over Limit storm.
  - August 2005 system upgrade from 32,768 to 65,536 nodes, which confounds any before/after comparison across August.
- **Liang DSN 2006:** bursty failures, 50% of network failures within 30 minutes; one midplane holds 26% of network failures.
- **DeepLog CCS 2017** §5.2: performance anomaly flagged on "Took * seconds to build instance".

## Ranked candidates
1. **A + B (recommended).** A: the R30-M0-N9-C:J16-U01 DTLB storm. B: the OpenStack slow spawns.
2. **C: Lustre /p/gb1 mount failures.**
   - 26,426 lines on 1,024 I/O nodes in 42 episodes, between 08-31 and 09-16 20:35.
   - Zero after 09-16.
   - Overlaps the August upgrade confounder.
3. **B alone.**
4. **D: KERNSTOR.** 3 waves on 22,528 nodes, every one at instruction address 0x00004ed8. Hypothesis: one faulting application.
5. **E: APPSEV chronic ciod.**

## Storyline for A
- **The storm:** 152,182 "data TLB error interrupt" lines from 06-11 17:17:44 to 23:39:32.
- **Before:** 0.29 s earlier, a KERNMC "L2 DCU read error" machine check.
- **After:** MMCS and ciodb restart 23 s after the storm ends.
- **Recurrence:** on 06-13 at 04:47:27 the same machine check fires again, followed by rts panic at 04:47:31.
- **Rarity:** "L2 DCU read error" occurs only 3 times in the whole corpus.

## SereneDB gotchas
- `regexp_extract` returns '' (not NULL) when nothing matches, so cast only after filtering.
- QUALIFY works.
- `quantile_cont` and `median` work.
- `time_bucket(INTERVAL '7 days', ts)` works.
- Episodes: compute `lag`, then a running `sum` window.
