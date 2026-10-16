"""Step 1: materialise problem-group membership (r4e_pg) + placeholder ownership, then per-group factors (r4e_factors).
Problem groups:
  BGL  kind='alert_tag' : alert_tag <> '-' (Oliner & Stearley expert labels)
  BGL  kind='pattern'   : masked message, level in FATAL/ERROR/WARNING/SEVERE/FAILURE
  OS   kind='pattern'   : masked message, level in ERROR/WARNING/CRITICAL
"""
from r4e_common import *
c = conn()
M = mask_sql()
log = []
def step(name, sql):
    cols, rows, dt = run(c, sql, quiet=True)
    print(f"{name}: {dt:.2f}s"); log.append((name, sql, dt))
    return cols, rows

# Placeholder Ownership Table (NOT the organisers' table — fictional, for demonstrating the Team-count factor only)
step("ownership", """CREATE OR REPLACE TABLE r4e_ownership_placeholder AS SELECT * FROM (VALUES
 ('BGL','KERNEL','PLACEHOLDER-Team-NodeOS'),
 ('BGL','APP','PLACEHOLDER-Team-JobRuntime'),
 ('BGL','MMCS','PLACEHOLDER-Team-ControlSystem'),
 ('BGL','BGLMASTER','PLACEHOLDER-Team-ControlSystem'),
 ('BGL','CMCS','PLACEHOLDER-Team-ControlSystem'),
 ('BGL','DISCOVERY','PLACEHOLDER-Team-HardwareOps'),
 ('BGL','HARDWARE','PLACEHOLDER-Team-HardwareOps'),
 ('BGL','MONITOR','PLACEHOLDER-Team-HardwareOps'),
 ('BGL','LINKCARD','PLACEHOLDER-Team-Interconnect'),
 ('BGL','SERV_NET','PLACEHOLDER-Team-Interconnect'),
 ('OpenStack','nova.osapi_compute.wsgi.server','PLACEHOLDER-Team-ComputeAPI'),
 ('OpenStack','nova.metadata.wsgi.server','PLACEHOLDER-Team-ComputeAPI'),
 ('OpenStack','nova.api.openstack.compute.server_external_events','PLACEHOLDER-Team-ComputeAPI'),
 ('OpenStack','nova.api.openstack.wsgi','PLACEHOLDER-Team-ComputeAPI'),
 ('OpenStack','nova.compute.manager','PLACEHOLDER-Team-ComputeNode'),
 ('OpenStack','nova.compute.claims','PLACEHOLDER-Team-ComputeNode'),
 ('OpenStack','nova.compute.resource_tracker','PLACEHOLDER-Team-ComputeNode'),
 ('OpenStack','oslo_service.periodic_task','PLACEHOLDER-Team-ComputeNode'),
 ('OpenStack','nova.virt.libvirt.imagecache','PLACEHOLDER-Team-Virtualization'),
 ('OpenStack','nova.virt.libvirt.driver','PLACEHOLDER-Team-Virtualization'),
 ('OpenStack','nova.scheduler.host_manager','PLACEHOLDER-Team-Scheduling'),
 ('OpenStack','keystonemiddleware.auth_token','PLACEHOLDER-Team-Identity')
) t(dataset, component, team)""")

step("pg", f"""CREATE OR REPLACE TABLE r4e_pg AS
SELECT 'BGL' dataset, 'alert_tag' kind, alert_tag gkey, b.ts, b.line_no, coalesce(node,'<null>') src,
       CASE WHEN node LIKE 'R%' THEN node END node,
       regexp_extract(node, '^(R[0-9]+)-', 1) rack, b.component, o.team
FROM bgl b LEFT JOIN r4e_ownership_placeholder o ON o.dataset='BGL' AND o.component=b.component
WHERE alert_tag <> '-'
UNION ALL
SELECT 'BGL', 'pattern', {M}, b.ts, b.line_no, coalesce(node,'<null>'),
       CASE WHEN node LIKE 'R%' THEN node END, regexp_extract(node, '^(R[0-9]+)-', 1), b.component, o.team
FROM bgl b LEFT JOIN r4e_ownership_placeholder o ON o.dataset='BGL' AND o.component=b.component
WHERE level IN ('FATAL','ERROR','WARNING','SEVERE','FAILURE') AND alert_tag = '-'
UNION ALL
SELECT 'OpenStack', 'pattern',
       first_value({M}) OVER (PARTITION BY s.log_file, s.ts, s.pid, s.level ORDER BY s.line_no),
       s.ts, s.line_no, coalesce(s.instance_id, s.log_file), s.instance_id, NULL, s.component, o.team
FROM openstack s LEFT JOIN r4e_ownership_placeholder o ON o.dataset='OpenStack' AND o.component=s.component
WHERE level IN ('ERROR','WARNING','CRITICAL')""")

# Universe C variant: BGL multi-line dumps (same node + same epoch second) collapsed under the first line's pattern.
# (arg_min aggregate + join; a first_value() window over the same rows was the query running when the shared server SIGSEGV'd once - not proven causal)
step("pg_c", f"""INSERT INTO r4e_pg
WITH r AS (SELECT b.line_no, b.ts, b.epoch, coalesce(node,'<null>') src, CASE WHEN node LIKE 'R%' THEN node END node,
                  regexp_extract(node, '^(R[0-9]+)-', 1) rack, b.component, o.team, {M} p
           FROM bgl b LEFT JOIN r4e_ownership_placeholder o ON o.dataset='BGL' AND o.component=b.component
           WHERE level IN ('FATAL','ERROR','WARNING','SEVERE','FAILURE') AND alert_tag = '-'),
h AS (SELECT src, epoch, arg_min(p, line_no) head FROM r GROUP BY 1,2)
SELECT 'BGL', 'pattern_c', h.head, r.ts, r.line_no, r.src, r.node, r.rack, r.component, r.team
FROM r JOIN h USING (src, epoch)""")

# Dataset clocks: end, recent window start, covered units (days for BGL, hours for OpenStack) recent/baseline.
# BGL: recent = last 28 days before dataset max ts. OpenStack spans ~2.7 days with gaps -> recent = last 6 h.
step("clock", """CREATE OR REPLACE TABLE r4e_clock AS
WITH b AS (SELECT max(ts) mx FROM bgl), o AS (SELECT max(ts) mx FROM openstack)
SELECT 'BGL' dataset, mx ds_end, mx - INTERVAL 28 DAY recent_start, 'day' unit,
  (SELECT count(DISTINCT date_trunc('day', ts)) FROM bgl WHERE ts >= mx - INTERVAL 28 DAY) u_recent,
  (SELECT count(DISTINCT date_trunc('day', ts)) FROM bgl WHERE ts <  mx - INTERVAL 28 DAY) u_base
FROM b
UNION ALL
SELECT 'OpenStack', mx, mx - INTERVAL 6 HOUR, 'hour',
  (SELECT count(DISTINCT date_trunc('hour', ts)) FROM openstack WHERE ts >= mx - INTERVAL 6 HOUR),
  (SELECT count(DISTINCT date_trunc('hour', ts)) FROM openstack WHERE ts <  mx - INTERVAL 6 HOUR)
FROM o""")
print(run(c, "SELECT * FROM r4e_clock")[1])

FACTORS = """CREATE OR REPLACE TABLE r4e_factors AS
WITH g AS (
  SELECT p.dataset, kind, gkey, count(*) n,
    count(DISTINCT date_trunc('day', ts)) active_days, count(DISTINCT date_trunc('hour', ts)) active_hours,
    count(DISTINCT node) nodes, count(DISTINCT rack) racks,
    count(DISTINCT component) comps, count(DISTINCT team) teams,
    min(ts) first_ts, max(ts) last_ts,
    count(*) FILTER (WHERE ts >= k.recent_start) n_recent,
    count(*) FILTER (WHERE ts <  k.recent_start) n_base,
    max(k.u_recent) u_recent, max(k.u_base) u_base, max(k.ds_end) ds_end
  FROM r4e_pg p JOIN r4e_clock k USING (dataset)
  GROUP BY 1,2,3),
mins AS (
  SELECT dataset, kind, gkey, date_trunc('minute', ts) m, count(*) c
  FROM r4e_pg GROUP BY 1,2,3,4),
rk AS (
  SELECT *, row_number() OVER (PARTITION BY dataset, kind, gkey ORDER BY c DESC) r,
         count(*) OVER (PARTITION BY dataset, kind, gkey) am
  FROM mins),
srcs AS (
  SELECT dataset, kind, gkey, src, count(*) c FROM r4e_pg GROUP BY 1,2,3,4),
conc AS (
  SELECT s.dataset, s.kind, s.gkey, count(*) sources, max(s.c) top_src_n,
         1.0 / sum(power(s.c::DOUBLE / t.n, 2)) eff_sources
  FROM srcs s JOIN (SELECT dataset, kind, gkey, sum(c) n FROM srcs GROUP BY 1,2,3) t USING (dataset, kind, gkey)
  GROUP BY 1,2,3),
burst AS (
  SELECT dataset, kind, gkey, max(am) active_minutes,
         sum(c) FILTER (WHERE r <= greatest(1, ceil(0.01 * am))) top1pct_entries
  FROM rk GROUP BY 1,2,3)
SELECT g.*, b.active_minutes, x.sources, x.top_src_n::DOUBLE / g.n AS top_src_share, x.eff_sources,
  b.top1pct_entries::DOUBLE / g.n AS burstiness,
  -- trend: recent rate / baseline rate per covered unit, add-one smoothed
  ((g.n_recent + 1.0) / (g.u_recent + 1.0)) / ((g.n_base + 1.0) / (g.u_base + 1.0)) AS trend_ratio,
  date_diff('hour', g.last_ts, g.ds_end) / 24.0 AS recency_days
FROM g JOIN burst b USING (dataset, kind, gkey) JOIN conc x USING (dataset, kind, gkey)"""
step("factors", FACTORS)
cols, rows, _ = run(c, "SELECT dataset, kind, count(*) groups, sum(n) entries FROM r4e_factors GROUP BY 1,2 ORDER BY 1,2")
print(cols, rows)
json.dump([(a, b, d) for a, b, d in log], open("r4e_build_log.json", "w"), indent=1)
