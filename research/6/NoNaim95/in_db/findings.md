# #6 in-DB template keys (subagent report, saved by parent; macros in mask_macros.sql, scoring in mask_ga.py)

## Recommendation
Build template keys with a `CREATE MACRO` chain of `regexp_replace` masks:
1. Apply the macro to `SELECT DISTINCT message` (358k distinct values, 7.5% of BGL rows).
2. Store the result as a dictionary table.
3. Join `template_id = substr(md5(key),1,16)` back onto the log rows.

## Accuracy (GA/FGA per Loghub-2.0 evaluator, computed in SQL over lh2 labels)
| Rules | BGL GA / FGA | OpenStack GA / FGA |
|---|---|---|
| g10, generic (11 rules) | 0.939 / 0.694 | 1.0 / 1.0 |
| v10, tuned (+8 BGL rules) | 0.9995 / 0.948 | 1.0 / 1.0 |
| Drain, published Loghub-2.0 full results (RQ2 csv) | 0.919 / 0.624 | 0.752 / 0.007 |

Caveat: the rules were tuned on the same labels they are scored against. There is no held-out set.

## Raw corpus
- BGL: 358,357 distinct messages map to 650 g10 keys and 476 v10 keys.
- OpenStack: 73 keys.

## Throughput (masking distinct messages first; ranges reflect server load)
| Rules | BGL | OpenStack |
|---|---|---|
| g10 | 0.3–1.1 s | 0.2–0.55 s |
| v10 | 0.9–2.5 s | 0.46–1.6 s |

Masking every row instead of distinct messages is 5–12× slower.

## lh2 templates as regex
- A regex join at 10–30k evaluations/s is infeasible: about 75 minutes for BGL.
- A generated CASE chain of 320 branches takes 5.1 s over distinct messages.
- Coverage of the raw corpus:
  - BGL: 97.54% of lines (4,631,261, exactly the lh2 line count). lh2 dropped the lines it could not template.
  - OpenStack: 99.91%. The unmatched lines are traceback lines.
- 143 v10 keys covering 116,635 lines (2.46%) have no lh2 equivalent.
- Use lh2 to name keys (majority EventId per key), not to classify lines.

## Storage and query cost
| Step | Time |
|---|---|
| Build dictionary | 1.4 s |
| Build narrow fact table | 1.9 s |
| ADD COLUMN + UPDATE | 2.7 s |
| GROUP BY template_id | 0.03 s |
| Daily trend, all templates | 0.08 s |
| Top-3 per component | 0.05 s |
| Query-time mask (for comparison) | 6.8 s |

- Generated STORED columns accept the macro.
- An inverted index on template_id plus message speeds up "search then count by template": 0.077 s, against 0.167 s with ILIKE.

## Other experiments
- **Loading Drain output:** COPY of 4.75M (line_no, template_id) rows takes 2.36 s. It depends on a stable line_no.
- **New patterns:** first seen in the last 7 days takes 0.03 s. Filter out corrupted concatenated lines with a minimum count.
- **OTel part_000** (1B rows, count in 0.6 s), g10 on the Body column:

  | Rows | Time | Rate | Keys |
  |---|---|---|---|
  | 10M | 7.1 s | 1.4M rows/s | 4,873 |
  | 100M | 290 s | 345k rows/s | 13,263 |

  Avoid `SELECT *`: it includes the MAP columns.

## Gotchas
- `regexp_replace` needs the 'g' flag to replace all matches.
- The regex engine is RE2: no lookaround.
- Regex join against a pattern column is very slow; use a CASE chain instead.
- Use md5 rather than hash(); hash() is not stable across versions.
- 34,470 NULL messages: wrap with coalesce.
- The server crashed 3 times with SIGSEGV during these runs.
