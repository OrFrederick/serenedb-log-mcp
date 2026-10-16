# #1 literature findings (subagent report, saved by parent; SQL files in this dir)

## Ground truth
O&S DSN'07 (4,747,963 lines; 348,460 alerts; 41 categories) says "filtered alerts 1,202" with a stated T = 5 s.

We re-implemented Algorithm 3.1 (per-category lag across all nodes):
- T = 5 s gives 1,643.
- **T = 300 s gives exactly 1,202.** T = 299 and T = 301 give the same.
- Per category: 9 of the paper's 10 printed categories match exactly. APPSEV gives 136 against 138; the other 31 categories give 521 against 519, so the total still matches.
- The parent agent independently re-verified this: 1202 / 37 / 136 / 105.

T sweep:

| T | 1 s | 5 s | 10 s | 30 s | 60 s | 120 s | 300 s | 600 s | 1800 s | 3600 s |
|---|---|---|---|---|---|---|---|---|---|---|
| Incidents | 2,851 | 1,643 | 1,511 | 1,361 | 1,309 | 1,257 | 1,202 | 1,149 | 1,006 | 914 |

Between 60 s and 600 s the count stays within ±7% of 1,202.

## Where the reduction comes from
Grouping per node with a 120 s gap still leaves 192,260 Bursts. The big reduction comes from merging across nodes.
- KERNDTLB: 152,182 lines on one node.
- KERNSTOR (2005-06-14 09:47): 20,480 lines on 20,480 nodes across 20 racks.
- KERNMNTF and APPSEV hit all 1,024 I/O nodes.

## Missing labels
ICDM'08 adds two categories that BGL leaves unlabelled: "Unable to steer" (243 lines) and "excessive soft failures" (41 lines). We found all 284 lines on 125 nodes, and every one has alert_tag '-'.

## OpenStack
The 4 labelled instances have 25 lines each and 0 ERROR.

| | Build time |
|---|---|
| The 4 labelled instances | 42.81, 30.32, 51.59, 32.12 s |
| Normal instances | median 20.5 s, p99 22.3 s, max 23.34 s |
| d7d68893 (unlabelled) | 27.61 s |

- This matches DeepLog §5.2, which throttles the network at 2 points. It does not match DeepLog's three injected error types.
- ERROR lines appear only in the normal files.
- Landauer et al. (arXiv 2309.02854) advise against using these OpenStack labels.

## Industry defaults
| Tool | Defaults |
|---|---|
| PagerDuty | IAG standard window 5 min (custom up to 3,600 s); incident < 24 h old; 1,000 alerts max; content-based window 5 min–24 h, extending |
| Alertmanager / Grafana | group_wait 30 s, group_interval 5 m, repeat_interval 4 h |
| Splunk ITSI | default episode break after a 7,200 s pause |
| Datadog | Watchdog needs ≥ 24 h of baseline; "severe" means ≥ 10 min; event correlation 500 alerts per case |
| Opsgenie | dedup by alias |
| Elastic | rare function needs ≥ 20 buckets |
| BigPanda | 120 min window |

## HPC filtering papers
**Liang 2005**
- Merges entries at the same location within 2 min.
- Cross-location windows: memory 60 s, network 15 min.
- "Larger than 5 minutes is undesirable."
- 828,387 entries reduce to 3,111.

**Liang 2007 ASF**
- 281,462 entries reduce to 998 with a 20 min filter, and to 835 with ASF.

## Topology
From Coteus 2005:
- 64 racks × 2 midplanes × 16 node cards per midplane.
- Each node card holds 16 compute cards × 2 chips.

In our data:
- 65,536 `-C:` locations and 1,024 `-I:` locations.
- NULL: 89,296 lines. UNKNOWN_LOCATION: 27,039 lines.

## Windowing in anomaly-detection papers
- DL anomaly-detection papers use fixed, sliding or session windows (loglizer, Le & Zhang ICSE'22). BGL per-node sessions give F1 0.994–1.0, which is trivial.
- The HPC filtering papers and industry tools use gap sessions instead.

## Spread of the 1,202 Incidents
| Spread | Incidents | Lines | Median duration |
|---|---|---|---|
| Single node | 721 | 153,407 | 0 s |
| One midplane | 119 | — | — |
| One rack | 3 | — | — |
| Multi-rack | 359 | 184,307 | 33 s |

No Incident lasts more than 24 h.

## Cascades
Per-node Burst A followed by B within 60 s:

| Cascade | Pairs | Days | Median lag |
|---|---|---|---|
| KERNTERM → KERNREC | 4,431 | 6 | 17.3 s |
| KERNMC → KERNRTSP | 144 | 88 | 17.6 s |
| APPSEV → KERNMNTF | 100 | 1 | — |
| KERNDTLB → KERNTERM | 64 | — | — |

## Proposed definitions
- **Pattern**
- **Burst:** per (source, Pattern), gap 300 s.
- **Incident:** per Pattern across sources, gap 300 s, optional 24 h cap.
- **Spread:** nodes, node cards, midplanes, racks, and top_node_share. Classes: single-source, localised, systemic (≥ 2 racks).
- **Chronic Pattern:** present in ≥ 50% of weeks.
- **New Pattern:** first seen in the last 7 days, or absent from a ≥ 24 h baseline.
- **Cascade:** follows within 60 s, on ≥ 2 days.
- **OpenStack Request Failure:** an ERROR or CRITICAL line.
- **Latency Degradation:** > 1.2 × the baseline p99, about 27 s.

## SereneDB gotchas
- INT32 overflow: cast to BIGINT.
- regexp_extract returns '' on no match: use TRY_CAST(NULLIF(..., '')).
- SIMILAR TO returned 0 rows. Use regexp_matches instead.
- The `epoch` column is UTC and `ts` is local time 7 h behind.
