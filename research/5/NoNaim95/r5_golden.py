#!/usr/bin/env python3
"""r5_golden.py - run the worked-investigation candidate queries on SereneDB 3x each,
print result rows and median wall time. Usage: python -I r5_golden.py [name ...]"""
import sys, time, statistics, psycopg

A = "('544fd51c-4edc-4780-baae-ba1d80a0acfc','ae651dff-c7ad-43d6-ac96-bbcd820ccca8','a445709b-6ad0-40ec-8860-bec60b6ca0c2','1643649d-2f42-4303-bfcd-7798baec19f9')"
Q = {
# --- whole Base Corpus aggregation (both Datasets) ---
"corpus_by_dataset": """
SELECT dataset, count(*) entries,
       sum(CASE WHEN sev IN ('FATAL','FAILURE','ERROR','SEVERE','CRITICAL') THEN 1 ELSE 0 END) error_or_worse,
       count(DISTINCT component) components, min(ts) first_ts, max(ts) last_ts
FROM (SELECT 'BGL' dataset, level sev, component, ts FROM bgl
      UNION ALL SELECT 'OpenStack', level, component, ts FROM openstack) u
GROUP BY 1 ORDER BY 1""",
# --- Candidate A: R30-M0-N9-C:J16-U01 data-TLB storm ---
"A_share_of_alerts": """
SELECT node, count(*) alerts, round(100.0*count(*)/sum(count(*)) OVER (),2) pct_of_all_alerts
FROM bgl WHERE alert_tag<>'-' GROUP BY 1 ORDER BY 2 DESC LIMIT 5""",
"A_search_dtlb": """
SELECT node, count(*) n, min(ts) first_ts, max(ts) last_ts, count(DISTINCT date_trunc('minute',ts)) active_minutes
FROM bgl WHERE message ILIKE '%data TLB error interrupt%' GROUP BY 1 ORDER BY 2 DESC LIMIT 5""",
"A_before": """
SELECT ts, alert_tag, level, message FROM bgl
WHERE node='R30-M0-N9-C:J16-U01' AND ts BETWEEN TIMESTAMP '2005-06-11 17:17:44.898186' - INTERVAL '2 hours' AND TIMESTAMP '2005-06-11 17:17:44.898186'
ORDER BY ts""",
"A_after": """
SELECT ts, node, component, message FROM bgl
WHERE ts > TIMESTAMP '2005-06-11 23:39:32.877740' AND ts < TIMESTAMP '2005-06-11 23:39:32.877740' + INTERVAL '5 minutes' ORDER BY ts""",
"A_recur_l2dcu": """
SELECT ts, node, alert_tag, message FROM bgl WHERE message ILIKE '%L2 DCU read error%' OR (node='R30-M0-N9-C:J16-U01' AND alert_tag='KERNRTSP') ORDER BY ts""",
"A_hourly": """
SELECT date_trunc('hour',ts) h, count(*) all_entries, sum(CASE WHEN alert_tag='KERNDTLB' THEN 1 ELSE 0 END) dtlb
FROM bgl WHERE ts >= '2005-06-11' AND ts < '2005-06-12 02:00' GROUP BY 1 ORDER BY 1""",
# --- Team Lead recurrence: episodes per alert category (30-min gap) ---
"TL_episodes": """
WITH e AS (SELECT alert_tag, ts, node,
  CASE WHEN lag(ts) OVER (PARTITION BY alert_tag ORDER BY ts) IS NULL
         OR epoch(ts)-epoch(lag(ts) OVER (PARTITION BY alert_tag ORDER BY ts)) > 1800 THEN 1 ELSE 0 END nb
  FROM bgl WHERE alert_tag<>'-'),
s AS (SELECT *, sum(nb) OVER (PARTITION BY alert_tag ORDER BY ts ROWS UNBOUNDED PRECEDING) sid FROM e)
SELECT alert_tag, count(*) entries, count(DISTINCT sid) episodes_30m_gap, count(DISTINCT node) nodes,
       count(DISTINCT date_trunc('day',ts)) days_active,
       count(DISTINCT split_part(node,'-',1)||'-'||split_part(node,'-',2)) midplanes
FROM s GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
"TL_weekly_top": """
SELECT time_bucket(INTERVAL '7 days', ts) week, alert_tag, count(*) n FROM bgl
WHERE alert_tag IN ('KERNDTLB','KERNSTOR','APPSEV','KERNMNTF','KERNTERM') GROUP BY 1,2 ORDER BY 1,2""",
# --- Candidate B: OpenStack slow-spawn anomalies ---
"B_spawn_dist": f"""
SELECT source_file, CASE WHEN instance_id IN {A} THEN 'labelled_anomaly' ELSE 'other' END grp, count(*) n,
  round(min(s),2) mn, round(median(s),2) p50, round(quantile_cont(s,0.95),2) p95, round(max(s),2) mx
FROM (SELECT source_file, instance_id, CAST(regexp_extract(message,'Took ([0-9.]+) seconds',1) AS DOUBLE) s
      FROM openstack WHERE message LIKE '%seconds to spawn the instance%') t GROUP BY 1,2 ORDER BY 1,2""",
"B_top_spawns": """
SELECT source_file, instance_id, ts, CAST(regexp_extract(message,'Took ([0-9.]+) seconds',1) AS DOUBLE) s
FROM openstack WHERE message LIKE '%seconds to spawn the instance%' ORDER BY s DESC LIMIT 6""",
"B_phase": f"""
WITH p AS (SELECT source_file, instance_id, CASE WHEN instance_id IN {A} THEN 'labelled_anomaly' ELSE 'other' END g,
  min(CASE WHEN message LIKE '%Creating image%' THEN epoch(ts) END) img,
  min(CASE WHEN message LIKE '%VM Started%' THEN epoch(ts) END) started,
  min(CASE WHEN message LIKE '%VM Resumed%' THEN epoch(ts) END) resumed
  FROM openstack WHERE instance_id IS NOT NULL GROUP BY 1,2,3)
SELECT source_file, g, count(*) n, round(median(started-img),2) img_to_started_p50, round(max(started-img),2) img_to_started_max,
  round(median(resumed-started),2) started_to_resumed_p50
FROM p WHERE started IS NOT NULL GROUP BY 1,2 ORDER BY 1,2""",
"B_errors_by_file": """
SELECT source_file, count(*) n, sum(CASE WHEN level IN ('ERROR','CRITICAL') THEN 1 ELSE 0 END) err,
  sum(CASE WHEN level='WARNING' THEN 1 ELSE 0 END) warn FROM openstack GROUP BY 1 ORDER BY 1""",
"B_api_latency": """
WITH w AS (SELECT source_file, regexp_extract(message,'"([A-Z]+) ',1) method,
  regexp_replace(regexp_replace(regexp_extract(message,'"[A-Z]+ ([^ ?]+)',1),
    '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}','{id}','g'),'/v2/[0-9a-f]{32}','/v2/{tenant}') path,
  CAST(regexp_extract(message,'time: ([0-9.]+)',1) AS DOUBLE) t
  FROM openstack WHERE component='nova.osapi_compute.wsgi.server')
SELECT method||' '||path endpoint, source_file, count(*) n, round(median(t),3) p50, round(quantile_cont(t,0.99),3) p99, round(max(t),3) mx
FROM w WHERE path IN ('/v2/{tenant}/servers','/v2/{tenant}/servers/detail') GROUP BY 1,2 ORDER BY 1,2""",
"B_timeline_search": """
SELECT ts, split_part(log_file,'.log',1) f, component, message FROM openstack
WHERE instance_id='a445709b-6ad0-40ec-8860-bec60b6ca0c2' OR message ILIKE '%a445709b-6ad0-40ec-8860-bec60b6ca0c2%' ORDER BY ts""",
}

def conn():
    for i in range(20):
        try: return psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
        except psycopg.OperationalError: time.sleep(3)
    raise SystemExit("db down")

def main():
    names = sys.argv[1:] or list(Q)
    c = conn()
    for n in names:
        for attempt in range(4):
            try:
                times = []
                for _ in range(3):
                    t = time.perf_counter(); cur = c.execute(Q[n]); rows = cur.fetchall(); times.append(time.perf_counter()-t)
                cols = [d.name for d in cur.description]
                break
            except psycopg.OperationalError as e:
                print(f"-- {n}: server error ({e.__class__.__name__}), reconnecting (concurrent crash?)"); time.sleep(5); c = conn()
        if True:
            print(f"=== {n}  rows={len(rows)}  median_wall={statistics.median(times)*1000:.0f}ms  runs={[round(x*1000) for x in times]}")
            print(" | ".join(cols))
            for r in rows[:40]: print(" | ".join(str(x)[:140] for x in r))
main()
