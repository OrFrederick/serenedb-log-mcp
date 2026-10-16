# #1 experiments findings (subagent report, saved by parent; SQL 01..08 + outputs/ here)

## Pattern key
- The masked key gives 5,197 distinct keys.
  - 55 keys cover 90% of entries.
  - 2,223 keys occur only once.
- alert_tag gives 41 categories, which map to 80 masked keys.

## Node forms
- Chip: 4,552,957 entries, 66,560 distinct (65,536 compute + 1,024 I/O).
- Node card: 73,545 entries.
- Link card: 3,909 entries.
- No location: 116,659 entries.

## Bursts (per node, pattern key)
| Key | Gap | Bursts |
|---|---|---|
| Masked | 5 s | 3.43M |
| Masked | 3600 s | 2.18M |
| Tag | 300 s | 190,290 |

- The median Burst has 1 entry.
- The largest Burst is 152,182 entries: KERNDTLB on R30-M0-N9-C:J16-U01.

## Incidents (Bursts of one key merged across nodes, gap G')
Tag key: 5 s gives 1,643; 60 s gives 1,309; **300 s gives 1,202**; 3600 s gives 914.

## Oliner & Stearley filter reproduction
- At T = 300 s our total is exactly 1,202, the published figure.
- 9 of the 10 printed categories match exactly. APPSEV is 136 vs 138; the remaining categories are 521 vs 519.
- The total stays at 1,202 for every T from 298 to 311 s.
- The paper text says T = 5 s. Source checked: Oliner's thesis, chapter 2.
- The filter runs in 0.32 s.

## Spread over the 1,202 Incidents
| Class | Incidents | Share of entries |
|---|---|---|
| Single node | 721 | 44% |
| Multi-rack | 359 | 52.9% |

- Incidents spanning 1,024 nodes are exactly all the I/O nodes (APPSEV, KERNMNTF, APPUNAV).
- Wide KERNSTOR, KERNTERM, KERNREC and KERNRTSP Incidents are compute chips with one entry each.

## Trickle
A slow trickle never becomes a single Incident; it needs the chronic view. Example: ddr "total of N errors" has 3,029 Incidents over 169 days.

## Cascades (1 h window, same node)
| Cascade | Support | Confidence | Lift |
|---|---|---|---|
| KERNMC → KERNRTSP | 181 | 0.60 | 312 |
| KERNTERM → KERNREC | — | — | 7.9 |
| KERNDTLB → KERNRTSP | — | — | 124 |

Masked-key cascades are polluted by multi-line records. Add a minimum lag of about 1 s.

## Chronic vs new
| Class | Patterns |
|---|---|
| Gone | 4,760 |
| Chronic (≥ 60 days) | 64 |
| Intermittent | 314 |
| New (last 14 days) | 59 |

- All correctable-error chatter stops on 2005-11-21 04:09. This is likely a logging change, so it is a trap for trend analysis.
- Largest z-scores: ddr suppressing, z = 69; Lustre mount FAILED, z = 42 (week of 2005-08-29).

## OpenStack
- The 4 labelled instances are the 4 slowest spawns: 50.72, 41.81, 31.27 and 29.46 s. The next slowest is 26.69 s.
- Their extra time falls between Creating image and VM Started.
- A Keystone 503 plus CRITICAL at 2017-05-17 06:25 is the only latency degradation: p95 3.36 s in one 5-minute bucket.

## Top Incidents
1–5. The KERNSTOR storm, 2005-06-13/14, up to 20,480 nodes across 20 racks.
6–7. KERNTERM followed by KERNREC on 2,048 nodes, 2005-11-15 19:18.
9–14. APPSEV, KERNMNTF and APPUNAV, each hitting all 1,024 I/O nodes.
15. The KERNDTLB single-node Burst.

## Gotchas
- `int/int` truncates.
- A window sum() returns HUGEINT. Grouping by it crashed the server (SIGSEGV) twice; casting to ::BIGINT fixed it. This is a correlation, not a proven cause.
- `bgl.epoch` is UTC; `ts` is local time, +7 h.
