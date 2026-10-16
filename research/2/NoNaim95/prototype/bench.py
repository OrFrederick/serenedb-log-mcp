"""Benchmark the SQL behind every draft tool on local SereneDB.

Each query runs 3 times with psycopg3 server-side parameter binding; prints the
median wall time (ms), row count and the first rows. Concurrency caveat: other
agents query the same instance, so numbers are noisy.

Usage: python bench.py [name-substring ...]
"""
import datetime as dt
import statistics
import sys
import time

import psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"

# Masks used by top_patterns / latency_stats (kept as SQL fragments, never user input)
MASK_BGL = ("regexp_replace(regexp_replace(regexp_replace(message, "
            "'0x[0-9a-fA-F]+', '<HEX>', 'g'), '[0-9]+', '<N>', 'g'), '\\s+', ' ', 'g')")
ENDPOINT = ("regexp_replace(regexp_replace(regexp_replace("
            "regexp_extract(message, '\"(GET|POST|PUT|DELETE|PATCH|HEAD) ([^ ?]+)', 1) || ' ' || "
            "regexp_extract(message, '\"(GET|POST|PUT|DELETE|PATCH|HEAD) ([^ ?]+)', 2), "
            "'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '{uuid}', 'g'), "
            "'[0-9a-f]{32}', '{tenant}', 'g'), '/[0-9]+', '/{n}', 'g')")
LAT = "regexp_extract(message, 'time: ([0-9.]+)', 1)::DOUBLE"

T0, T1 = dt.datetime(2005, 6, 1), dt.datetime(2006, 2, 1)

Q = {
    # ---------------- search_logs ----------------
    "search.topk_bm25_filters": (
        """SELECT line_no, ts, node, component, level,
                  BM25(r2p_bgl_idx.tableoid) AS score,
                  ts_highlight(r2p_bgl_idx.message, 'MaxWords=25') AS snippet
           FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s)
             AND ts >= %s AND ts < %s AND component IN (%s)
           ORDER BY score DESC, line_no LIMIT %s""",
        ["ciod failed", T0, T1, "APP", 20]),
    "search.count_exact": (
        """SELECT count(*) FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s)
             AND ts >= %s AND ts < %s AND component IN (%s)""",
        ["ciod failed", T0, T1, "APP"]),
    "search.topk_with_window_count": (
        """SELECT line_no, ts, node, component, level,
                  BM25(r2p_bgl_idx.tableoid) AS score, count(*) OVER () AS total
           FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s)
             AND ts >= %s AND ts < %s AND component IN (%s)
           ORDER BY score DESC, line_no LIMIT %s""",
        ["ciod failed", T0, T1, "APP", 20]),
    "search.topk_plus_count_one_stmt": (
        """WITH m AS (SELECT count(*) AS total FROM r2p_bgl_idx
                      WHERE message @@ websearch_to_tsquery(%s) AND ts >= %s AND ts < %s),
                t AS (SELECT line_no, ts, node, component, level, BM25(r2p_bgl_idx.tableoid) AS score
                      FROM r2p_bgl_idx
                      WHERE message @@ websearch_to_tsquery(%s) AND ts >= %s AND ts < %s
                      ORDER BY score DESC, line_no LIMIT %s)
           SELECT t.*, m.total FROM t CROSS JOIN m ORDER BY score DESC, line_no""",
        ["ciod failed", T0, T1, "ciod failed", T0, T1, 20]),
    "search.broad_term_topk": (  # very frequent term, worst case for ranking
        """SELECT line_no, ts, node, BM25(r2p_bgl_idx.tableoid) AS score
           FROM r2p_bgl_idx WHERE message @@ %s ORDER BY score DESC, line_no LIMIT %s""",
        ["error", 20]),
    "search.broad_term_count": (
        "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ %s", ["error"]),
    "search.newest_first": (  # chronological ordering instead of BM25
        """SELECT line_no, ts, node, component, level, message FROM r2p_bgl_idx
           WHERE message @@ %s AND level IN (%s, %s) ORDER BY ts DESC, line_no DESC LIMIT %s""",
        ["error", "FATAL", "FAILURE", 20]),
    "search.phrase": (
        """SELECT line_no, ts, node, ts_highlight(r2p_bgl_idx.message) AS snippet
           FROM r2p_bgl_idx WHERE message @@ ts_phrase(%s) ORDER BY ts LIMIT %s""",
        ["kernel panic", 20]),
    "search.phrase_websearch_quotes": (
        """SELECT count(*) FROM r2p_bgl_idx WHERE message @@ websearch_to_tsquery(%s)""",
        ['"machine check interrupt"']),
    "search.fuzzy": (
        """SELECT line_no, ts, node, BM25(r2p_bgl_idx.tableoid) AS score,
                  ts_highlight(r2p_bgl_idx.message) AS snippet
           FROM r2p_bgl_idx WHERE message @@ ts_levenshtein(%s, %s)
           ORDER BY score DESC, line_no LIMIT %s""",
        ["interupt", 1, 20]),
    "search.fuzzy_count": (
        "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ ts_levenshtein(%s, %s)", ["interupt", 1]),
    "search.openstack_topk": (
        """SELECT line_no, ts, source_file, component, level, request_id, instance_id,
                  BM25(r2p_os_idx.tableoid) AS score, ts_highlight(r2p_os_idx.message) AS snippet
           FROM r2p_os_idx WHERE message @@ websearch_to_tsquery(%s) AND level IN (%s, %s)
           ORDER BY score DESC, line_no LIMIT %s""",
        ["instance error", "ERROR", "WARNING", 20]),
    "search.FINAL_scoreNULL_filters": (  # recommended shape: filters do not perturb BM25
        """SELECT line_no, ts, node, component, level,
                  BM25(r2p_bgl_idx.tableoid) AS score,
                  ts_highlight(r2p_bgl_idx.message, 'MaxWords=25') AS snippet
           FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s)
             AND ts @@ ts_between(%s, %s, true, false)::score(NULL)
             AND component @@ ts_any(%s::varchar[])::score(NULL)
           ORDER BY score DESC, line_no LIMIT %s""",
        ["ciod failed", T0, T1, ["APP"], 20]),
    "search.FINAL_count": (
        """SELECT count(*) FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s)
             AND ts @@ ts_between(%s, %s, true, false)::score(NULL)
             AND component @@ ts_any(%s::varchar[])::score(NULL)""",
        ["ciod failed", T0, T1, ["APP"]]),
    "search.os_error_level": (
        """SELECT line_no, ts, component, level, instance_id, BM25(r2p_os_idx.tableoid) AS score,
                  ts_highlight(r2p_os_idx.message) AS snippet
           FROM r2p_os_idx WHERE message @@ %s AND level @@ ts_any(%s::varchar[])::score(NULL)
           ORDER BY score DESC, line_no LIMIT %s""",
        ["instance", ["ERROR", "WARNING"], 20]),
    # ---------------- get_context ----------------
    "context.bgl_same_node_before": (
        """SELECT line_no, ts, component, level, message FROM bgl
           WHERE node = %s AND (ts, line_no) < (%s, %s)
           ORDER BY ts DESC, line_no DESC LIMIT %s""",
        ["R03-M0-N4-I:J18-U11", dt.datetime(2005, 7, 7, 22, 49, 47, 314458), 1577127, 10]),
    "context.bgl_same_node_before_idx": (
        """SELECT line_no, ts, component, level FROM r2p_bgl_idx
           WHERE node = %s AND ts <= %s AND line_no < %s
           ORDER BY ts DESC, line_no DESC LIMIT %s""",
        ["R03-M0-N4-I:J18-U11", dt.datetime(2005, 7, 7, 22, 49, 47, 314458), 1577127, 10]),
    "context.bgl_same_node_both_union": (
        """(SELECT line_no, ts, component, level, message FROM bgl
            WHERE node = %s AND line_no < %s ORDER BY line_no DESC LIMIT %s)
           UNION ALL
           (SELECT line_no, ts, component, level, message FROM bgl
            WHERE node = %s AND line_no >= %s ORDER BY line_no LIMIT %s)
           ORDER BY line_no""",
        ["R03-M0-N4-I:J18-U11", 1577127, 10, "R03-M0-N4-I:J18-U11", 1577127, 11]),
    "context.bgl_time_window_all_nodes": (
        """SELECT line_no, ts, node, component, level, message FROM bgl
           WHERE ts BETWEEN %s::TIMESTAMP - %s::INTERVAL AND %s::TIMESTAMP + %s::INTERVAL
           ORDER BY ts, line_no LIMIT %s""",
        [dt.datetime(2005, 7, 7, 22, 49, 47), "30 seconds", dt.datetime(2005, 7, 7, 22, 49, 47), "30 seconds", 50]),
    "context.bgl_time_window_count": (
        """SELECT count(*), count(DISTINCT node) FROM bgl
           WHERE ts BETWEEN %s::TIMESTAMP - %s::INTERVAL AND %s::TIMESTAMP + %s::INTERVAL""",
        [dt.datetime(2005, 7, 7, 22, 49, 47), "30 seconds", dt.datetime(2005, 7, 7, 22, 49, 47), "30 seconds"]),
    "context.os_same_instance": (
        """SELECT line_no, ts, component, level, request_id, message FROM openstack
           WHERE instance_id = %s ORDER BY ts, line_no LIMIT %s""",
        ["544fd51c-4edc-4780-baae-ba1d80a0acfc", 50]),
    "context.os_same_request": (
        """SELECT line_no, ts, component, level, message FROM openstack
           WHERE request_id = %s ORDER BY ts, line_no LIMIT %s""",
        ["req-d6143bd4-9784-4e95-a0c8-83fba00530b6", 50]),
    # ---------------- count_by ----------------
    "count_by.component_bgl": (
        """SELECT component AS key, count(*) AS n FROM bgl WHERE ts >= %s AND ts < %s
           GROUP BY 1 ORDER BY n DESC LIMIT %s""", [T0, T1, 50]),
    "count_by.level_bgl_filtered": (
        """SELECT level AS key, count(*) AS n FROM bgl WHERE component = %s
           GROUP BY 1 ORDER BY n DESC LIMIT %s""", ["KERNEL", 50]),
    "count_by.node_top": (
        """SELECT node AS key, count(*) AS n, count(*) OVER () AS n_groups FROM bgl
           WHERE level IN (%s, %s) GROUP BY 1 ORDER BY n DESC LIMIT %s""", ["FATAL", "FAILURE", 20]),
    "count_by.rack": (
        """SELECT split_part(node, '-', 1) AS key, count(*) AS n FROM bgl
           WHERE alert_tag <> '-' GROUP BY 1 ORDER BY n DESC LIMIT %s""", [20]),
    "count_by.alert_tag": (
        """SELECT alert_tag AS key, count(*) AS n, count(DISTINCT node) AS nodes FROM bgl
           WHERE alert_tag <> '-' GROUP BY 1 ORDER BY n DESC LIMIT %s""", [50]),
    "count_by.dataset_whole_corpus": (
        """SELECT 'BGL' AS dataset, level, count(*) AS n FROM bgl GROUP BY 1, 2
           UNION ALL
           SELECT 'OpenStack', level, count(*) FROM openstack GROUP BY 1, 2
           ORDER BY dataset, n DESC""", []),
    "count_by.fts_by_component": (
        """SELECT component AS key, count(*) AS n FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s) GROUP BY 1 ORDER BY n DESC LIMIT %s""",
        ["error", 50]),
    "count_by.fts_by_component_os": (
        """SELECT component AS key, count(*) AS n FROM r2p_os_idx
           WHERE message @@ websearch_to_tsquery(%s) GROUP BY 1 ORDER BY n DESC LIMIT %s""",
        ["instance", 50]),
    # ---------------- trend ----------------
    "trend.day_bgl": (
        """SELECT time_bucket(%s::INTERVAL, ts) AS bucket, count(*) AS n FROM bgl
           WHERE ts >= %s AND ts < %s AND level IN (%s, %s) GROUP BY 1 ORDER BY 1""",
        ["1 day", T0, T1, "FATAL", "FAILURE"]),
    "trend.week_by_component": (
        """SELECT time_bucket(%s::INTERVAL, ts) AS bucket, component, count(*) AS n FROM bgl
           WHERE alert_tag <> '-' GROUP BY 1, 2 ORDER BY 1, 3 DESC""",
        ["7 days"]),
    "trend.hour_fts": (
        """SELECT time_bucket(%s::INTERVAL, ts) AS bucket, count(*) AS n FROM r2p_bgl_idx
           WHERE message @@ websearch_to_tsquery(%s) GROUP BY 1 ORDER BY 1""",
        ["1 hour", "ciod failed"]),
    "trend.date_trunc_day_os": (
        """SELECT date_trunc('hour', ts) AS bucket, level, count(*) AS n FROM openstack
           GROUP BY 1, 2 ORDER BY 1""", []),
    "compare_periods.component": (
        """WITH a AS (SELECT component k, count(*) n FROM bgl WHERE ts >= %s AND ts < %s AND alert_tag <> '-' GROUP BY 1),
                b AS (SELECT component k, count(*) n FROM bgl WHERE ts >= %s AND ts < %s AND alert_tag <> '-' GROUP BY 1)
           SELECT coalesce(a.k, b.k) AS key, coalesce(a.n, 0) AS a_n, coalesce(b.n, 0) AS b_n,
                  coalesce(b.n, 0) - coalesce(a.n, 0) AS delta,
                  round(coalesce(b.n, 0)::DOUBLE / nullif(a.n, 0), 3) AS ratio
           FROM a FULL OUTER JOIN b ON a.k = b.k ORDER BY abs(delta) DESC LIMIT %s""",
        [dt.datetime(2005, 8, 1), dt.datetime(2005, 9, 1), dt.datetime(2005, 9, 1), dt.datetime(2005, 10, 1), 20]),
    "compare_periods.filter_agg": (
        """SELECT alert_tag AS key,
                  count(*) FILTER (WHERE ts >= %s AND ts < %s) AS a_n,
                  count(*) FILTER (WHERE ts >= %s AND ts < %s) AS b_n
           FROM bgl WHERE alert_tag <> '-' AND ts >= %s AND ts < %s
           GROUP BY 1 ORDER BY abs(b_n - a_n) DESC LIMIT %s""",
        [dt.datetime(2005, 8, 1), dt.datetime(2005, 9, 1), dt.datetime(2005, 9, 1), dt.datetime(2005, 10, 1),
         dt.datetime(2005, 8, 1), dt.datetime(2005, 10, 1), 20]),
    "trend.day_gapfilled": (
        """WITH b AS (SELECT unnest(generate_series(%s::TIMESTAMP, %s::TIMESTAMP, %s::INTERVAL)) AS bucket),
                c AS (SELECT time_bucket(%s::INTERVAL, ts) AS bucket, count(*) AS n FROM bgl
                      WHERE ts >= %s AND ts < %s AND level IN (%s, %s) GROUP BY 1)
           SELECT b.bucket, coalesce(c.n, 0) AS n FROM b LEFT JOIN c USING (bucket) ORDER BY 1""",
        [dt.datetime(2005, 6, 3), dt.datetime(2006, 1, 4), "1 day", "1 day", T0, T1, "FATAL", "FAILURE"]),
    # ---------------- top_patterns ----------------
    "top_patterns.bgl_all": (
        f"""SELECT {MASK_BGL} AS pattern, count(*) AS n, count(DISTINCT node) AS nodes,
                   min(ts) AS first_ts, max(ts) AS last_ts, min(line_no) AS example_line
            FROM bgl GROUP BY 1 ORDER BY n DESC LIMIT %s""", [20]),
    "top_patterns.bgl_fatal": (
        f"""SELECT {MASK_BGL} AS pattern, count(*) AS n, count(DISTINCT node) AS nodes
            FROM bgl WHERE level IN (%s, %s) GROUP BY 1 ORDER BY n DESC LIMIT %s""",
        ["FATAL", "FAILURE", 20]),
    "top_patterns.bgl_fts": (
        f"""SELECT {MASK_BGL} AS pattern, count(*) AS n FROM r2p_bgl_idx
            WHERE message @@ %s GROUP BY 1 ORDER BY n DESC LIMIT %s""",
        ["error", 20]),
    "top_patterns.bgl_lh2_template": (  # alternative: Loghub-2.0 template ids (different row set!)
        """SELECT event_id, any_value(event_template) AS template, count(*) AS n FROM lh2_bgl
           GROUP BY 1 ORDER BY n DESC LIMIT %s""", [20]),
    "top_patterns.os_all": (
        """SELECT regexp_replace(regexp_replace(regexp_replace(message,
               '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', 'g'),
               '[0-9a-f]{32}', '<HEX>', 'g'), '[0-9]+(\\.[0-9]+)*', '<N>', 'g') AS pattern,
                  count(*) AS n FROM openstack GROUP BY 1 ORDER BY n DESC LIMIT %s""", [20]),
    # ---------------- latency_stats ----------------
    "latency.endpoint_overall": (
        f"""SELECT {ENDPOINT} AS endpoint, count(*) AS n,
                   round(quantile_cont({LAT}, 0.5), 4) AS p50,
                   round(quantile_cont({LAT}, 0.95), 4) AS p95,
                   round(quantile_cont({LAT}, 0.99), 4) AS p99,
                   round(max({LAT}), 4) AS max_s
            FROM openstack WHERE message LIKE '%%time: %%'
            GROUP BY 1 ORDER BY n DESC LIMIT %s""", [20]),
    "latency.endpoint_bucket_list_quantile": (
        f"""SELECT time_bucket(%s::INTERVAL, ts) AS bucket, {ENDPOINT} AS endpoint, count(*) AS n,
                   quantile_cont({LAT}, [0.5, 0.95, 0.99]) AS p
            FROM openstack WHERE message LIKE '%%time: %%' AND source_file = %s
            GROUP BY 1, 2 ORDER BY 1, n DESC""", ["1 hour", "openstack_normal2.log"]),
    "latency.approx": (
        f"""SELECT {ENDPOINT} AS endpoint, count(*) n, approx_quantile({LAT}, 0.99) AS p99
            FROM openstack WHERE message LIKE '%%time: %%' GROUP BY 1 ORDER BY n DESC LIMIT %s""", [20]),
    "latency.instance_lifecycle_abnormal": (  # spawn time per instance (anomaly hint)
        """SELECT instance_id, min(ts) AS first_ts, max(ts) AS last_ts,
                  epoch(max(ts)) - epoch(min(ts)) AS span_s, count(*) AS n
           FROM openstack WHERE instance_id IS NOT NULL AND source_file = %s
           GROUP BY 1 ORDER BY span_s DESC LIMIT %s""", ["openstack_abnormal.log", 10]),
    "latency.precomputed_table": (
        """SELECT endpoint, count(*) AS n, quantile_cont(latency_s, [0.5, 0.95, 0.99]) AS p
           FROM r2p_os_http WHERE ts >= %s AND ts < %s GROUP BY 1 ORDER BY n DESC LIMIT %s""",
        [dt.datetime(2017, 5, 14), dt.datetime(2017, 5, 18), 20]),
    "top_patterns.precomputed_col": (
        """SELECT pattern, count(*) AS n, count(DISTINCT node) AS nodes FROM r2p_bgl_pat
           WHERE level IN (%s, %s) GROUP BY 1 ORDER BY n DESC LIMIT %s""", ["FATAL", "FAILURE", 20]),
    # ---------------- describe_corpus ----------------
    "describe.datasets": (
        """SELECT 'BGL' AS dataset, count(*) AS n, min(ts) AS t0, max(ts) AS t1,
                  count(DISTINCT component) AS components, count(DISTINCT node) AS sources
           FROM bgl
           UNION ALL
           SELECT 'OpenStack', count(*), min(ts), max(ts), count(DISTINCT component), count(DISTINCT log_file)
           FROM openstack""", []),
    "describe.components": (
        """SELECT 'BGL' AS dataset, component, count(*) AS n FROM bgl GROUP BY 1, 2
           UNION ALL SELECT 'OpenStack', component, count(*) FROM openstack GROUP BY 1, 2
           ORDER BY 1, 3 DESC""", []),
}


def inline_limit(sql, params):
    """WORKAROUND: a bound LIMIT parameter on an ORDER BY BM25 index scan segfaults
    SereneDB 26.10.1 (reproduced 3x). Inline the (int-validated) limit for index queries."""
    if "_idx" in sql and sql.rstrip().endswith("LIMIT %s"):
        sql = sql.rstrip()[:-2] + str(int(params[-1]))
        params = params[:-1]
    return sql, params


def run(conn, sql, params, n=3):
    sql, params = inline_limit(sql, params)
    times, rows = [], None
    for _ in range(n):
        t = time.perf_counter()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        times.append((time.perf_counter() - t) * 1000)
    return statistics.median(times), times, rows


def main():
    sel = sys.argv[1:]
    with psycopg.connect(DSN, autocommit=True, cursor_factory=psycopg.ClientCursor) as conn:
        for name, (sql, params) in Q.items():
            if sel and not any(s in name for s in sel):
                continue
            try:
                med, times, rows = run(conn, sql, params)
                print(f"## {name}: median {med:.1f} ms  runs={[round(x, 1) for x in times]}  rows={len(rows)}")
                for r in rows[:4]:
                    print("   ", tuple(str(x)[:110] for x in r))
            except Exception as e:  # noqa: BLE001
                print(f"## {name}: ERROR {type(e).__name__}: {str(e).splitlines()[0][:300]}")


if __name__ == "__main__":
    main()
