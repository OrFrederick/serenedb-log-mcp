"""Benchmark the SQL behind each proposed tool. psycopg3 server-side parameter binding.
Runs each query 1 cold + 3 warm, reports median warm ms and row counts."""
import json, statistics, time, psycopg, sys
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
Q = {}
# ---- search_logs (BGL) : websearch syntax, filters, BM25, highlight, total via window
Q["search_bm25_window_total"] = ("""
SELECT line_no, ts, node, component, level, BM25(r2_bgl_fts.tableoid) AS score,
       ts_highlight(r2_bgl_fts.content, 'StartSel=«, StopSel=», MaxWords=40') AS snippet,
       count(*) OVER () AS total_matches
FROM r2_bgl_fts
WHERE content @@ websearch_to_tsquery(%(q)s)
  AND ts >= %(start)s AND ts < %(end)s
ORDER BY score DESC, ts LIMIT %(limit)s""", dict(q='"control stream" socket', start='2005-06-01', end='2006-02-01', limit=20))
Q["search_bm25_no_total"] = ("""
SELECT line_no, ts, node, component, level, BM25(r2_bgl_fts.tableoid) AS score,
       ts_highlight(r2_bgl_fts.content, 'StartSel=«, StopSel=», MaxWords=40') AS snippet
FROM r2_bgl_fts
WHERE content @@ websearch_to_tsquery(%(q)s) AND ts >= %(start)s AND ts < %(end)s
ORDER BY score DESC, ts LIMIT %(limit)s""", dict(q='"control stream" socket', start='2005-06-01', end='2006-02-01', limit=20))
Q["search_separate_count"] = ("""
SELECT count(*) FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery(%(q)s) AND ts >= %(start)s AND ts < %(end)s""",
    dict(q='"control stream" socket', start='2005-06-01', end='2006-02-01'))
Q["search_broad_window_total"] = ("""
SELECT line_no, ts, BM25(r2_bgl_fts.tableoid) AS score, count(*) OVER () AS total_matches
FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery(%(q)s) ORDER BY score DESC LIMIT 20""", dict(q='error'))
Q["search_broad_separate_count"] = ("""
SELECT count(*) FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery(%(q)s)""", dict(q='error'))
Q["search_broad_no_total"] = ("""
SELECT line_no, ts, BM25(r2_bgl_fts.tableoid) AS score FROM r2_bgl_fts
WHERE content @@ websearch_to_tsquery(%(q)s) ORDER BY score DESC LIMIT 20""", dict(q='error'))
Q["search_time_order_filters"] = ("""
SELECT line_no, ts, node, component, level, content, count(*) OVER () AS total_matches
FROM r2_bgl_fts
WHERE content @@ websearch_to_tsquery(%(q)s) AND component = %(component)s AND list_contains(%(levels)s::text[], level)
  AND ts >= %(start)s AND ts < %(end)s
ORDER BY ts LIMIT %(limit)s""", dict(q='parity', component='KERNEL', levels=['FATAL','ERROR'], start='2005-06-01', end='2006-02-01', limit=20))
Q["search_fuzzy"] = ("""
SELECT line_no, ts, ts_highlight(r2_bgl_fts.content) AS snippet, count(*) OVER () AS total_matches
FROM r2_bgl_fts WHERE content @@ ts_levenshtein(%(term)s, %(dist)s) ORDER BY ts LIMIT 10""", dict(term='instrucion', dist=1))
Q["search_phrase_slop"] = ("""
SELECT count(*) FROM r2_bgl_fts WHERE content @@ ts_phrase(%(p)s)""", dict(p='machine check interrupt'))
# ---- search_logs (OpenStack)
Q["os_search"] = ("""
SELECT line_no, ts, component, level, instance_id, request_id, ts_highlight(r2_os_fts.content) AS snippet,
       count(*) OVER () AS total_matches
FROM r2_os_fts WHERE content @@ websearch_to_tsquery(%(q)s) ORDER BY BM25(r2_os_fts.tableoid) DESC, ts LIMIT 10""", dict(q='"Unknown base file"'))
# ---- get_context
Q["context_same_node_by_time"] = ("""
WITH a AS (SELECT ts, node FROM bgl WHERE line_no = %(line)s)
(SELECT b.line_no, b.ts, b.node, b.level, b.content FROM bgl b, a WHERE b.node = a.node AND b.ts <= a.ts AND b.ts >= a.ts - %(win)s::interval ORDER BY b.ts DESC, b.line_no DESC LIMIT %(n)s)
UNION ALL
(SELECT b.line_no, b.ts, b.node, b.level, b.content FROM bgl b, a WHERE b.node = a.node AND b.ts > a.ts AND b.ts <= a.ts + %(win)s::interval ORDER BY b.ts, b.line_no LIMIT %(n)s)""",
    dict(line=827110, win='1 hour', n=10))
Q["context_same_node_via_index"] = ("""
WITH a AS (SELECT ts, node FROM r2_bgl_fts WHERE line_no = %(line)s)
(SELECT b.line_no, b.ts, b.level, b.content FROM r2_bgl_fts b, a WHERE b.node = a.node AND b.ts <= a.ts AND b.ts >= a.ts - %(win)s::interval ORDER BY b.ts DESC LIMIT %(n)s)
UNION ALL
(SELECT b.line_no, b.ts, b.level, b.content FROM r2_bgl_fts b, a WHERE b.node = a.node AND b.ts > a.ts AND b.ts <= a.ts + %(win)s::interval ORDER BY b.ts LIMIT %(n)s)""",
    dict(line=827110, win='1 hour', n=10))
Q["context_global_by_line"] = ("""
SELECT line_no, ts, node, component, level, content FROM bgl
WHERE line_no BETWEEN %(line)s - %(n)s AND %(line)s + %(n)s ORDER BY line_no""", dict(line=827110, n=5))
Q["context_os_request"] = ("""
SELECT line_no, ts, component, level, content FROM openstack WHERE request_id = %(rid)s ORDER BY ts, line_no LIMIT 50""",
    dict(rid=None))  # filled at runtime
Q["context_os_instance"] = ("""
SELECT line_no, ts, component, level, request_id, content, count(*) OVER () AS total FROM openstack
WHERE instance_id = %(iid)s ORDER BY ts, line_no LIMIT 50""", dict(iid=None))
# ---- count_by
Q["count_by_component_all"] = ("""
SELECT component AS key, count(*) AS n, count(*) FILTER (WHERE alert_label <> '-') AS alert_n
FROM bgl GROUP BY 1 ORDER BY n DESC""", {})
Q["count_by_rack_query"] = ("""
SELECT rack AS key, count(*) AS n, count(DISTINCT node) AS distinct_nodes, min(ts) AS first_seen, max(ts) AS last_seen
FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery(%(q)s) GROUP BY 1 ORDER BY n DESC LIMIT %(top)s""", dict(q='parity', top=10))
Q["count_by_node_level"] = ("""
SELECT node AS key, count(*) AS n FROM bgl WHERE list_contains(%(levels)s::text[], level) GROUP BY 1 ORDER BY n DESC LIMIT %(top)s""", dict(levels=['FATAL','FAILURE'], top=10))
Q["count_by_with_other_bucket"] = ("""
WITH g AS (SELECT node AS k, count(*) AS n, min(ts) AS first_seen, max(ts) AS last_seen FROM bgl
           WHERE list_contains(%(levels)s::text[], level) GROUP BY 1),
r AS (SELECT *, row_number() OVER (ORDER BY n DESC, k) AS rk FROM g)
SELECT CASE WHEN rk <= %(top)s THEN k ELSE '(other)' END AS bucket_key, sum(n) AS n,
       count(*) AS groups_in_bucket, min(first_seen) AS first_seen, max(last_seen) AS last_seen
FROM r GROUP BY 1 ORDER BY (bucket_key = '(other)'), n DESC""", dict(levels=['FATAL','FAILURE'], top=10))
Q["count_by_dataset_whole_corpus"] = ("""
SELECT 'bgl' AS dataset, level, count(*) AS n FROM bgl GROUP BY ALL
UNION ALL SELECT 'openstack', level, count(*) FROM openstack GROUP BY ALL ORDER BY dataset, n DESC""", {})
# ---- trend
Q["trend_day_query"] = ("""
SELECT time_bucket(%(bucket)s::interval, ts) AS bucket, count(*) AS n, count(DISTINCT node) AS nodes
FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery(%(q)s) AND ts >= %(start)s AND ts < %(end)s
GROUP BY 1 ORDER BY 1""", dict(bucket='1 day', q='parity', start='2005-06-01', end='2006-02-01'))
Q["trend_week_component_split"] = ("""
SELECT time_bucket(%(bucket)s::interval, ts) AS bucket, component, count(*) AS n
FROM bgl WHERE list_contains(%(levels)s::text[], level) GROUP BY ALL ORDER BY 1, 3 DESC""", dict(bucket='7 days', levels=['FATAL','FAILURE']))
Q["trend_zero_fill"] = ("""
WITH b AS (SELECT unnest(generate_series(%(start)s::timestamp, %(end)s::timestamp - INTERVAL '1 second', %(bucket)s::interval)) AS bucket),
c AS (SELECT time_bucket(%(bucket)s::interval, ts, %(start)s::timestamp) AS bucket, count(*) AS n FROM r2_bgl_fts
      WHERE content @@ websearch_to_tsquery(%(q)s) AND ts >= %(start)s AND ts < %(end)s GROUP BY 1)
SELECT b.bucket, coalesce(c.n, 0) AS n FROM b LEFT JOIN c USING (bucket) ORDER BY 1""", dict(bucket='1 day', q='parity', start='2005-09-01', end='2005-10-01'))
# ---- compare_periods
Q["compare_periods_component"] = ("""
SELECT component AS key,
  count(*) FILTER (WHERE ts >= %(a0)s AND ts < %(a1)s) AS baseline_n,
  count(*) FILTER (WHERE ts >= %(b0)s AND ts < %(b1)s) AS current_n
FROM bgl WHERE list_contains(%(levels)s::text[], level) AND ((ts >= %(a0)s AND ts < %(a1)s) OR (ts >= %(b0)s AND ts < %(b1)s))
GROUP BY 1 ORDER BY current_n - baseline_n DESC LIMIT %(top)s""",
    dict(a0='2005-08-01', a1='2005-09-01', b0='2005-09-01', b1='2005-10-01', levels=['FATAL','FAILURE'], top=10))
# ---- top_patterns (fallback: regex masking until template_id exists)
Q["top_patterns_regex_mask"] = ("""
SELECT regexp_replace(content, '(0x[0-9a-fA-F]+|[0-9a-f]{8,}|[0-9]+(\\.[0-9]+)*|/[^ ]*)', '<*>', 'g') AS pattern,
       count(*) AS n, count(DISTINCT node) AS nodes, min(line_no) AS example_line
FROM bgl WHERE list_contains(%(levels)s::text[], level) AND ts >= %(start)s AND ts < %(end)s
GROUP BY 1 ORDER BY n DESC LIMIT %(top)s""", dict(levels=['FATAL','FAILURE','ERROR','SEVERE'], start='2005-06-01', end='2006-02-01', top=10))
Q["top_patterns_regex_mask_os"] = ("""
SELECT regexp_replace(content, '([0-9a-f]{8}-[0-9a-f-]{27}|req-[0-9a-f-]+|[0-9]+(\\.[0-9]+)*)', '<*>', 'g') AS pattern,
       count(*) AS n FROM openstack WHERE level IN ('ERROR','WARNING') GROUP BY 1 ORDER BY n DESC LIMIT 10""", {})
# ---- latency_stats
Q["latency_by_path"] = ("""
SELECT regexp_replace(http_path, '[0-9a-f]{8}-[0-9a-f-]{27}', '{id}', 'g') AS key, http_method, count(*) AS n,
  round(quantile_cont(latency_s, 0.5), 4) AS p50, round(quantile_cont(latency_s, 0.95), 4) AS p95,
  round(quantile_cont(latency_s, 0.99), 4) AS p99, round(max(latency_s), 4) AS max_s
FROM openstack WHERE latency_s IS NOT NULL GROUP BY ALL ORDER BY n DESC LIMIT 10""", {})
Q["latency_by_source_file"] = ("""
SELECT source_file AS key, count(*) AS n, quantile_cont(latency_s, [0.5, 0.95, 0.99]) AS p
FROM openstack WHERE latency_s IS NOT NULL GROUP BY 1 ORDER BY 1""", {})
Q["latency_trend"] = ("""
SELECT time_bucket(INTERVAL '1 hour', ts) AS bucket, count(*) AS n, quantile_cont(latency_s, 0.95) AS p95
FROM openstack WHERE latency_s IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 5""", {})
Q["instance_lifecycle_spawn_time"] = ("""
SELECT instance_id, count(*) AS n, max(ts) - min(ts) AS span,
  max(TRY_CAST(regexp_extract(content, 'Took ([0-9.]+) seconds to spawn', 1) AS DOUBLE)) AS spawn_s
FROM openstack WHERE instance_id IS NOT NULL GROUP BY 1 ORDER BY spawn_s DESC NULLS LAST LIMIT 5""", {})

def main():
    only = set(sys.argv[1:])
    out = {}
    with psycopg.connect(DSN, autocommit=True) as c:
        rid = c.execute("select request_id from openstack where request_id is not null and level='INFO' limit 1 offset 500").fetchone()[0]
        iid = c.execute("select instance_id from openstack where instance_id is not null limit 1 offset 500").fetchone()[0]
        Q["context_os_request"][1]["rid"] = rid; Q["context_os_instance"][1]["iid"] = iid
        for name, (sql, params) in Q.items():
            if only and name not in only: continue
            times = []; rows = None; err = None
            for i in range(4):
                t = time.perf_counter()
                try:
                    rows = c.execute(sql, params).fetchall()
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"; break
                times.append((time.perf_counter() - t) * 1000)
            if err:
                print(f"{name}: ERROR {err}"); out[name] = {"error": err}; continue
            med = statistics.median(times[1:])
            print(f"{name}: cold {times[0]:.0f} ms, warm median {med:.0f} ms, rows {len(rows)}")
            for r in rows[:4]:
                print("    ", tuple(str(x)[:110] for x in r))
            out[name] = {"cold_ms": round(times[0]), "warm_median_ms": round(med), "rows": len(rows),
                         "sample": [[str(x)[:200] for x in r] for r in rows[:5]], "sql": sql.strip(), "params": {k: str(v) for k, v in params.items()}}
    json.dump(out, open("bench_results.json" if not only else "bench_partial.json", "w"), indent=1, default=str)
main()
