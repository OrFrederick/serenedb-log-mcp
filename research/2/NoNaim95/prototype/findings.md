# #2 prototype findings (subagent report, saved by parent)
Stack: SereneDB 26.10.1, psycopg 3.3.6, mcp 2.3.0, Claude Code 2.1.293.

## Crashes (SIGSEGV, reproduced 3/3)
- Server-side bound `LIMIT $1` on a BM25-ordered search crashes when the term matches 557k rows.
- Bound `ts @@ ts_between($2,$3,...)::score(NULL)` also crashes.
- Workaround: psycopg `ClientCursor`. The injection test string was quoted safely.

## Filters and scoring
- Plain SQL filters on the index add to the BM25 score: top score 4.64 with no filter, 6.64 with a time filter, 8.79 with all filters.
- Fix: write the filters as `ts @@ ts_between(...)::score(NULL)` and `component @@ ts_any(%s::VARCHAR[])::score(NULL)`. The query takes 143 ms instead of 883 ms.

## Query semantics
- A bare `@@ 'a b'` is OR. Use `websearch_to_tsquery(%s)` for AND, and for "quoted phrases".
- Exact total: run a separate count statement (about 20 ms), then the top-k query. Using `count(*) OVER ()` kills top-k and takes 882 ms.
- `time_bucket` drops empty buckets, so gap-fill with `generate_series`.

## Safety
- `statement_timeout` is not enforced. `conn.cancel_safe()` works.
- A read-only role can still run `read_csv('/etc/passwd')`.

## Indexes
- BGL inverted index (freq+pos+offset, 5 INCLUDE columns): 3.2–14.6 s to build, 236 MB.
  - message-only: 118 MB. The base table is 273 MB.
- OpenStack index: 0.43 s, 21 MB.
- `VACUUM (REFRESH_TABLE)` takes 5–13 ms after `CREATE INDEX`.

## Latencies (median of 3)
| Operation | Latency |
|---|---|
| describe_corpus | 136 + 45 ms |
| search count | 20 ms |
| search rows | 32–143 ms |
| phrase / fuzzy / newest | 9 / 68 / 19 ms |
| get_context | 13–28 ms |
| count_by | 16–66 ms |
| FTS-filtered count_by | 11–15 ms |
| trend | 9–36 ms |
| compare_periods | 17 ms |
| top_patterns, regex at query time | 11.1 s whole BGL |
| top_patterns, precomputed | 175 ms |
| top_patterns, lh2 event_id | 19 ms |
| latency_stats, regex | 1.18 s |
| latency_stats, precomputed | 25 ms |

Conclusion: materialise pattern, endpoint, latency_s, status and rack at load time.

## Output size (structuredContent chars/4)
| Call | Tokens |
|---|---|
| search limit 20 | ~2.0k |
| search limit 50 | ~4.7k |
| context 10+10 | ~2.2k |
| count_by 25 groups | ~550 |
| trend 216 buckets | ~2.9k |
| tools/list, 5 tools | 5.1k (60% is outputSchema) |

## Claude Code smoke runs (2 runs, $0.43)
- Engineer run: search_logs (phrase), then get_context. Correct.
- Team Lead run: count_by + describe_corpus, then trend. Correct.

## Proposed limits
- search: default 20, max 50, messages cut at 240 chars.
- context: default 10, max 50 per side.
- count_by: default 25, max 100.
- trend: max 400 buckets, with a recovery error.

## Bonus finding
Ranking OpenStack instances by lifecycle span puts the 4 labelled anomalies on top (11 ms).
