# #6 survey findings (subagent report, saved by parent; benches + CSVs in this dir)

## Key facts
- **Loghub-2.0 lines join onto the raw corpus by an ordered text merge.**
  - BGL: all 4,631,261 lines appear in raw, byte-identical and in order; the merge takes about 2 s. The raw corpus has 116,702 extra lines.
  - OpenStack: Loghub-2.0 = abnormal + normal1 + normal2, minus 188 lines.
- **Loghub-2.0 dropped error and alert lines.**
  - OpenStack: 184 of 194 ERROR lines are gone.
  - BGL: 10 whole alert categories are gone (APPREAD 5,983, APPUNAV 2,048, …). In total 16,238 of 348,460 alert lines are missing.
- **BGL templates line up almost 1:1 with alert categories.** 27 of the 31 remaining tags map to exactly one template. Only 4 of 320 templates mix alert and non-alert lines.

## Official Loghub-2.0 full-dataset results (RQ2 CSVs)
| Parser | BGL GA | BGL FGA | BGL time | OpenStack GA | OpenStack FGA |
|---|---|---|---|---|---|
| Drain | .919 | .624 | 498 s | .752 | .007 |
| AEL | .915 | — | — | — | — |
| IPLoM | .896 | — | — | — | — |

Brain (from LUNAR): BGL 94.0/40.2/75.6/19.7, OpenStack 100/…/100.

## LLM parsers
- **Reported BGL GA:** LUNAR 95.5, AdaParser 93.5, LILAC 89.4.
- **Reproducibility:** reruns differ by up to about 40 GA points.
- **Cost and setup:** they need an API key or a GPU.
- **Licences:** several ship without a licence.
- **Verdict:** reject.

## Local measurements
| Run | GA / FGA | Wall time | Lines/s |
|---|---|---|---|
| Drain3 0.9.11, BGL, th .5 / d4 | .925 / .736 | 44–52 s | 88–105k |
| Drain3, OpenStack, naive | .345 | — | — |
| Drain3, OpenStack, tuned (IP-list / path / uuid masks, th .9 / d5) | .972 / .938 | 4.1 s | — |
| Brain (logparser3), OpenStack | 1.0 / 1.0 | 48.5 s | — |
| Brain, BGL first 1M lines | .998 / .957 | 251 s | — |

Brain on the BGL 1M-line run used 1.85 GB of memory.

## Production systems
- Grafana Loki: Drain fork. Depth 30, sim 0.3, max 300 clusters, 3 h retention.
- OTel Collector: contrib drainprocessor (alpha, 2026-04), go-drain3.
- Elastic: token-list categorizer, Platinum licence.
- Datadog: patterns over 10k sampled logs.

## Maintenance
- Drain3: last release 0.9.11 (2022). It pins jsonpickle==1.5.1, which Snyk flags as critical.
- drain3-improved 0.10.0 (MIT, 2026-04) is a maintained fork.

## Recommendation
1. Join the Loghub-2.0 gold templates by ordered merge.
2. Fill the gaps and new data (OTel) with a Drain3 fallback using per-Dataset settings.
3. Record which source produced each template in a `template_source` column.
4. Reject LLM parsers.
