"""Step 3: Evidence per Role for the SAME issue (BGL APPSEV: ciod CioStream 'Link has been severed').
Each query is run 3x; median wall time recorded (shared server, concurrent load -> noisy). Writes r4e_evidence_ledger.json + .md"""
import json, statistics, sys, datetime, decimal
from r4e_common import *
c = conn()
LEDGER = []
def ev(role, purpose, sql, scope, limit=None, total_sql=None, reps=3):
    times = []
    for _ in range(reps):
        cols, rows, dt = run(c, sql, quiet=True); times.append(dt)
    total = None
    if total_sql:
        total = run(c, total_sql, quiet=True)[1][0][0]
    e = dict(id=f"E{len(LEDGER)+1}", role=role, purpose=purpose, data_scope=scope, sql=sql.strip(),
             total_matches=None if total is None else int(total), returned=len(rows),
             truncated=(total is not None and int(total) > len(rows)), median_s=round(statistics.median(times), 3),
             runs_s=[round(t, 3) for t in times], cols=cols,
             rows=[[(str(v) if isinstance(v, (datetime.datetime, decimal.Decimal)) else v) for v in r] for r in rows])
    LEDGER.append(e); print(e["id"], role, purpose, f"{e['returned']} rows / total {e['total_matches']}  {e['median_s']}s")
    return e

# ---------------- Engineer ----------------
# E1 full-text (inverted index r2p_bgl_idx built by another researcher; phrase query) + time filter, limited
ev("Engineer", "Full-text search: phrase 'Link has been severed' in last 48h of BGL, latest 10",
   """SELECT ts, node, component, level, alert_tag FROM r2p_bgl_idx
      WHERE message @@ '"Link has been severed"' AND ts >= TIMESTAMP '2006-01-02 08:00:05'
      ORDER BY ts DESC LIMIT 10""",
   "BGL; ts >= 2006-01-02 08:00:05 (last 48h of Dataset); phrase 'Link has been severed'", 10,
   """SELECT count(*) FROM r2p_bgl_idx WHERE message @@ '"Link has been severed"' AND ts >= TIMESTAMP '2006-01-02 08:00:05'""")
# E2 first entry of that burst (raw line)
ev("Engineer", "First APPSEV Log Entry of the 2006-01-03 burst (raw line)",
   """SELECT line_no, ts, node, raw FROM bgl WHERE alert_tag = 'APPSEV' AND ts >= TIMESTAMP '2006-01-03' ORDER BY ts LIMIT 3""",
   "BGL; alert_tag=APPSEV; ts >= 2006-01-03", 3,
   """SELECT count(*) FROM bgl WHERE alert_tag = 'APPSEV' AND ts >= TIMESTAMP '2006-01-03'""")
# E3 nearby events on the SAME node: 5 entries before / 5 after by time (the I/O node logs sparsely)
ev("Engineer", "Same-node context: 5 Log Entries before and 5 after line 4746674 on node R22-M1-N8-I:J18-U11",
   """(SELECT line_no, ts, level, alert_tag, left(message, 110) message FROM bgl
       WHERE node = 'R22-M1-N8-I:J18-U11' AND ts < TIMESTAMP '2006-01-03 03:25:05.760549' ORDER BY ts DESC LIMIT 5)
      UNION ALL
      (SELECT line_no, ts, level, alert_tag, left(message, 110) message FROM bgl
       WHERE node = 'R22-M1-N8-I:J18-U11' AND ts >= TIMESTAMP '2006-01-03 03:25:05.760549' ORDER BY ts LIMIT 6)
      ORDER BY ts""",
   "BGL; node=R22-M1-N8-I:J18-U11; 5 before / anchor + 5 after", 11,
   """SELECT count(*) FROM bgl WHERE node = 'R22-M1-N8-I:J18-U11'""")
# E4 nearby events system-wide (any node) 5 min before .. 1 min after
ev("Engineer", "System-wide nearby events 03:20:05..03:26:05 grouped by alert_tag/component/level, with first node + first message",
   """SELECT alert_tag, component, level, count(*) n, count(DISTINCT node) nodes, min(ts) first_ts, max(ts) last_ts,
             arg_min(node, ts) first_node, arg_min(left(message, 80), ts) first_message
      FROM bgl WHERE ts BETWEEN TIMESTAMP '2006-01-03 03:20:05' AND TIMESTAMP '2006-01-03 03:26:05'
      GROUP BY 1,2,3 ORDER BY first_ts""",
   "BGL; all nodes; 2006-01-03 03:20:05..03:26:05; no level filter", None)
# E5 endpoint: which target address do APPSEV entries name (whole Dataset)
ev("Engineer", "Target endpoint named in APPSEV messages (regexp_extract), whole BGL",
   """SELECT regexp_extract(message, 'socket to ([0-9.]+):', 1) target, count(*) n, count(DISTINCT node) nodes,
             count(*) FILTER (WHERE node LIKE '%-I:%') io_node_entries
      FROM bgl WHERE alert_tag = 'APPSEV' GROUP BY 1 ORDER BY 2 DESC LIMIT 5""",
   "BGL; alert_tag=APPSEV; full Dataset", 5)

# ---------------- Team Lead ----------------
ev("Team Lead", "Weekly APPSEV counts: entries, distinct nodes, racks, midplanes (time_bucket 7d)",
   """SELECT time_bucket(INTERVAL 7 DAY, ts) week, count(*) entries, count(DISTINCT node) nodes,
             count(DISTINCT regexp_extract(node, '^(R[0-9]+)-', 1)) racks, count(DISTINCT date_trunc('day', ts)) active_days
      FROM bgl WHERE alert_tag = 'APPSEV' GROUP BY 1 ORDER BY 1""",
   "BGL; alert_tag=APPSEV; 2005-06-03..2006-01-04, weekly buckets", None)
ev("Team Lead", "ciod problem family per week: APPSEV vs untagged ciod patterns (Component APP)",
   """SELECT time_bucket(INTERVAL 7 DAY, ts) week,
             count(*) FILTER (WHERE alert_tag = 'APPSEV') appsev_socket_severed,
             count(*) FILTER (WHERE alert_tag = '-' AND message LIKE 'ciod: Error loading%') ciod_load_errors,
             count(*) FILTER (WHERE alert_tag = '-' AND message LIKE 'ciod: LOGIN chdir%') ciod_login_chdir,
             count(*) FILTER (WHERE alert_tag NOT IN ('-', 'APPSEV')) other_app_alerts
      FROM bgl WHERE component = 'APP' AND ts >= TIMESTAMP '2005-10-01' GROUP BY 1 ORDER BY 1""",
   "BGL; component=APP; ts >= 2005-10-01; weekly", None)
ev("Team Lead", "APPSEV recurrence: active days, gaps between active days, entries per active day",
   """WITH d AS (SELECT date_trunc('day', ts) day, count(*) n FROM bgl WHERE alert_tag = 'APPSEV' GROUP BY 1),
           g AS (SELECT day, n, date_diff('day', lag(day) OVER (ORDER BY day), day) gap FROM d)
      SELECT count(*) active_days, min(day) first_day, max(day) last_day, median(gap) median_gap_days, max(gap) max_gap_days,
             median(n) median_entries_per_active_day, max(n) max_entries_per_day
      FROM g""",
   "BGL; alert_tag=APPSEV; full Dataset", None)
ev("Team Lead", "APPSEV Bursts (gap>300 s) preceded within 120 s by another alert_tag (any node)",
   """WITH a AS (SELECT ts, CASE WHEN lag(ts) OVER (ORDER BY ts) IS NULL OR date_diff('second', lag(ts) OVER (ORDER BY ts), ts) > 300 THEN 1 ELSE 0 END new_burst FROM bgl WHERE alert_tag = 'APPSEV'),
      b AS (SELECT ts, sum(new_burst) OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) burst_id FROM a),
      bs AS (SELECT burst_id, min(ts) start_ts, count(*) n FROM b GROUP BY 1),
      pre AS (SELECT bs.burst_id, k.alert_tag FROM bs JOIN bgl k ON k.ts >= bs.start_ts - INTERVAL 120 SECOND AND k.ts < bs.start_ts AND k.alert_tag NOT IN ('-', 'APPSEV'))
      SELECT (SELECT count(*) FROM bs) bursts, (SELECT median(n) FROM bs) median_entries_per_burst,
        count(DISTINCT burst_id) with_any_other_alert_120s_before,
        count(DISTINCT burst_id) FILTER (WHERE alert_tag = 'KERNMC') with_kernmc,
        count(DISTINCT burst_id) FILTER (WHERE alert_tag = 'KERNRTSP') with_kernrtsp
      FROM pre""",
   "BGL; alert_tag=APPSEV sessionised into Bursts (new Burst after >300 s gap); precursor window 120 s; full Dataset", None)
ev("Team Lead", "Baseline for E-precursor: same test at 3618 probe times (each covered hour + 30 min)",
   """WITH h AS (SELECT DISTINCT date_trunc('hour', ts) + INTERVAL 30 MINUTE t FROM bgl),
      pre AS (SELECT DISTINCT h.t, k.alert_tag FROM h JOIN bgl k ON k.ts >= h.t - INTERVAL 120 SECOND AND k.ts < h.t AND k.alert_tag NOT IN ('-', 'APPSEV'))
      SELECT (SELECT count(*) FROM h) probes, count(DISTINCT t) with_any_other_alert_120s_before,
        count(DISTINCT t) FILTER (WHERE alert_tag = 'KERNMC') with_kernmc, count(DISTINCT t) FILTER (WHERE alert_tag = 'KERNRTSP') with_kernrtsp
      FROM pre""",
   "BGL; probe = every covered hour start + 30 min; precursor window 120 s; full Dataset", None)
ev("Team Lead", "Rack spread of APPSEV, last 4 weeks vs earlier (top 10 racks by recent entries)",
   """SELECT regexp_extract(node, '^(R[0-9]+)-', 1) rack,
             count(*) FILTER (WHERE ts >= TIMESTAMP '2005-12-07 08:00:05') last_4w,
             count(*) FILTER (WHERE ts <  TIMESTAMP '2005-12-07 08:00:05') before,
             count(DISTINCT node) nodes
      FROM bgl WHERE alert_tag = 'APPSEV' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10""",
   "BGL; alert_tag=APPSEV; split at 2005-12-07 08:00:05 (Dataset end - 28 d)", 10,
   """SELECT count(DISTINCT regexp_extract(node, '^(R[0-9]+)-', 1)) FROM bgl WHERE alert_tag = 'APPSEV'""")
ev("Team Lead", "Base Corpus: non-INFO entries by Dataset and PLACEHOLDER Team (whole corpus aggregation)",
   """SELECT dataset, team, count(*) entries, count(DISTINCT component) components FROM (
        SELECT 'BGL' dataset, b.component, o.team FROM bgl b LEFT JOIN r4e_ownership_placeholder o ON o.dataset='BGL' AND o.component=b.component WHERE b.level <> 'INFO'
        UNION ALL
        SELECT 'OpenStack', s.component, o.team FROM openstack s LEFT JOIN r4e_ownership_placeholder o ON o.dataset='OpenStack' AND o.component=s.component WHERE s.level <> 'INFO')
      GROUP BY 1,2 ORDER BY 1, 3 DESC""",
   "Base Corpus (BGL+OpenStack), level<>INFO, PLACEHOLDER Ownership Table", None)

# ---------------- Manager ----------------
COMPOSITE_SQL = """WITH u AS (
  SELECT *, CASE dataset WHEN 'BGL' THEN 3618 ELSE 32 END cov_hours,
            CASE dataset WHEN 'BGL' THEN 214.7 ELSE 2.68 END span_days,
            CASE dataset WHEN 'BGL' THEN active_days / 215.0 ELSE active_hours / 32.0 END persistence
  FROM r4e_factors WHERE kind = 'alert_tag' OR dataset = 'OpenStack'),
x AS (
  SELECT dataset, gkey, n, nodes, racks, eff_sources, top_src_share, persistence, trend_ratio, recency_days, burstiness, teams,
    log10(1 + n / (cov_hours / 24.0)) f_raw, ln(eff_sources) s_raw,
    (CASE WHEN n_recent + n_base < 30 THEN 0 ELSE greatest(-3, least(3, log2(trend_ratio))) END + 3) / 6.0 t01,
    1 - greatest(0, least(1, recency_days / span_days)) r01
  FROM u),
m AS (
  SELECT *,
    (f_raw - min(f_raw) OVER ()) / nullif(max(f_raw) OVER () - min(f_raw) OVER (), 0) f01,
    (s_raw - min(s_raw) OVER ()) / nullif(max(s_raw) OVER () - min(s_raw) OVER (), 0) s01,
    (persistence - min(persistence) OVER ()) / nullif(max(persistence) OVER () - min(persistence) OVER (), 0) p01,
    coalesce((teams - min(teams) OVER ()) / nullif(max(teams) OVER () - min(teams) OVER (), 0), 0) tm01
  FROM x)
SELECT row_number() OVER (ORDER BY 0.25*f01 + 0.25*s01 + 0.15*p01 + 0.15*t01 + 0.10*r01 + 0.10*tm01 DESC) rank,
  dataset, left(gkey, 40) problem_group, n entries, nodes, racks, round(eff_sources, 1) effective_sources,
  round(top_src_share, 3) top_source_share, round(persistence, 3) persistence, round(trend_ratio, 3) trend_4w,
  round(recency_days, 1) last_seen_days_before_end,
  round(0.25*f01 + 0.25*s01 + 0.15*p01 + 0.15*t01 + 0.10*r01 + 0.10*tm01, 3) score,
  rank() OVER (ORDER BY n DESC) raw_count_rank
FROM m ORDER BY score DESC LIMIT 10"""
ev("Manager", "Ranked risk table, composite formula (b) computed in SereneDB over measured factors", COMPOSITE_SQL,
   "Universe A: 41 BGL alert_tag groups (full BGL) + 7 OpenStack ERROR/WARNING/CRITICAL patterns (full OpenStack)", 10,
   "SELECT count(*) FROM r4e_factors WHERE kind = 'alert_tag' OR dataset = 'OpenStack'")
ev("Manager", "Raw-count leader check: KERNDTLB concentration (share of top node)",
   """SELECT node, count(*) n, round(count(*)::DOUBLE / sum(count(*)) OVER (), 4) share FROM bgl WHERE alert_tag = 'KERNDTLB' GROUP BY 1 ORDER BY 2 DESC LIMIT 3""",
   "BGL; alert_tag=KERNDTLB; full Dataset", 3, "SELECT count(DISTINCT node) FROM bgl WHERE alert_tag = 'KERNDTLB'")
ev("Manager", "Base Corpus totals by Dataset: entries, non-INFO entries, alert-tagged entries",
   """SELECT 'BGL' dataset, count(*) entries, count(*) FILTER (WHERE level <> 'INFO') non_info, count(*) FILTER (WHERE alert_tag <> '-') alert_tagged FROM bgl
      UNION ALL SELECT 'OpenStack', count(*), count(*) FILTER (WHERE level <> 'INFO'), NULL FROM openstack""",
   "Base Corpus, no filters", None)

json.dump(LEDGER, open("r4e_evidence_ledger.json", "w"), indent=1, default=str)
md = ["# r4e Evidence Ledger (generated by r4e_evidence.py)\n"]
for e in LEDGER:
    md += [f"\n## {e['id']} — {e['role']}: {e['purpose']}\n", f"- Data Scope: {e['data_scope']}",
           f"- total_matches: {e['total_matches']}, returned: {e['returned']}, truncated: {e['truncated']}, median wall {e['median_s']} s (runs {e['runs_s']})\n",
           "```sql\n" + e["sql"] + "\n```\n", md_table(e["cols"], e["rows"], 40)]
open("r4e_evidence_ledger.md", "w").write("\n".join(md))
