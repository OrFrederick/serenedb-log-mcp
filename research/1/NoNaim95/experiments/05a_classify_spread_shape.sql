-- 05a_classify_spread_shape.sql: (a) single-node spam vs multi-node and (b) short burst vs slow trickle,
-- on alert Incidents (key = alert_tag, G' = 300 s; equals Oliner-Stearley filtered alerts).

-- entry-level Incident id: gap sessionisation on category, node ignored
DROP TABLE IF EXISTS r1e_ent;
CREATE TABLE r1e_ent AS
WITH a AS (SELECT line_no, t, alert_tag k, coalesce(node, '<null>') node, rack, midplane, nodecard,
                  t - lag(t) OVER (PARTITION BY alert_tag ORDER BY t, line_no) gap
           FROM r1e_bgl WHERE alert_tag <> '-')
SELECT *, (sum(CASE WHEN gap IS NULL OR gap >= 300 THEN 1 ELSE 0 END) OVER (PARTITION BY k ORDER BY t, line_no ROWS UNBOUNDED PRECEDING))::BIGINT AS iid
FROM a;

-- per-Incident shape and spread features
DROP TABLE IF EXISTS r1e_feat;
CREATE TABLE r1e_feat AS
WITH e AS (SELECT *, CASE WHEN row_number() OVER (PARTITION BY k, iid ORDER BY t, line_no) = 1 THEN NULL ELSE gap END AS igap,
                  floor(t / 60)::BIGINT AS minute FROM r1e_ent),
     pm AS (SELECT k, iid, minute, count(*) c FROM e GROUP BY k, iid, minute),
     pmx AS (SELECT k, iid, max(c) max_per_min, count(*) active_minutes FROM pm GROUP BY k, iid),
     nn AS (SELECT k, iid, node, count(*) c FROM e GROUP BY k, iid, node),
     nx AS (SELECT k, iid, max(c) top_node_n, arg_max(node, c) top_node FROM nn GROUP BY k, iid),
     base AS (SELECT k, iid, min(t) t0, max(t) t1, count(*) n, count(DISTINCT node) nodes,
                     count(DISTINCT rack) racks, count(DISTINCT midplane) midplanes, count(DISTINCT nodecard) nodecards,
                     count(*) FILTER (WHERE rack IS NULL) nonstd, avg(igap) mean_gap, stddev_samp(igap) sd_gap
              FROM e GROUP BY k, iid)
SELECT b.*, b.t1 - b.t0 AS dur_s, p.max_per_min, p.active_minutes,
       (b.n::double) / greatest((b.t1 - b.t0) / 60.0, 1.0) AS mean_per_min,
       p.max_per_min / ((b.n::double) / greatest((b.t1 - b.t0) / 60.0, 1.0)) AS burstiness,
       b.sd_gap / nullif(b.mean_gap, 0) AS cv_gap,
       x.top_node, x.top_node_n, (x.top_node_n::double) / b.n AS top_node_share,
       CASE WHEN b.nodes = 1 THEN 'single-node'
            WHEN (x.top_node_n::double) / b.n >= 0.9 THEN 'node-dominated'
            WHEN b.racks >= 2 THEN 'multi-rack'
            WHEN b.midplanes >= 2 THEN 'multi-midplane'
            ELSE 'multi-node-local' END AS spread_class,
       CASE WHEN b.n = 1 THEN 'single-entry'
            WHEN b.t1 - b.t0 <= 600 THEN 'short-burst'
            WHEN b.t1 - b.t0 >= 3600 AND p.max_per_min / ((b.n::double) / greatest((b.t1 - b.t0) / 60.0, 1.0)) < 3 THEN 'sustained-steady'
            WHEN b.t1 - b.t0 >= 3600 THEN 'sustained-intermittent'
            ELSE 'medium' END AS shape_class
FROM base b JOIN pmx p USING (k, iid) JOIN nx x USING (k, iid);

-- cross-tab of the two classifications
SELECT spread_class, shape_class, count(*) incidents, sum(n) entries, median(nodes) med_nodes, median(dur_s) med_dur_s
FROM r1e_feat GROUP BY ALL ORDER BY incidents DESC;

-- spread classes alone (who is "one node spamming" vs systemic)
SELECT spread_class, count(*) incidents, sum(n) entries, round(100.0 * sum(n) / (SELECT sum(n) FROM r1e_feat), 2) pct_entries,
       max(n) max_entries, max(nodes) max_nodes
FROM r1e_feat GROUP BY spread_class ORDER BY entries DESC;

-- the contrast the issue asks for: biggest node-dominated vs widest multi-rack Incidents
SELECT 'most entries' sel, k, iid, make_timestamp_ms((t0 * 1000)::bigint) start_ts, round(dur_s) dur_s, n, nodes, racks, midplanes,
       round(top_node_share, 3) top_share, top_node, spread_class, shape_class, round(burstiness, 1) burstiness, round(cv_gap, 1) cv_gap
FROM r1e_feat QUALIFY row_number() OVER (ORDER BY n DESC) <= 8
UNION ALL
SELECT 'widest spread', k, iid, make_timestamp_ms((t0 * 1000)::bigint), round(dur_s), n, nodes, racks, midplanes,
       round(top_node_share, 3), top_node, spread_class, shape_class, round(burstiness, 1), round(cv_gap, 1)
FROM r1e_feat QUALIFY row_number() OVER (ORDER BY racks DESC, nodes DESC) <= 8
ORDER BY sel, n DESC;

-- shape sanity: sustained Incidents (a slow trickle at low rate is NOT one Incident at G'=300 s;
-- it shows up at pattern level as many small Incidents over many days, see 05c)
SELECT k, make_timestamp_ms((t0 * 1000)::bigint) start_ts, round(dur_s / 3600, 1) dur_h, n, nodes, round(mean_per_min, 2) mean_per_min,
       max_per_min, round(burstiness, 1) burstiness, round(cv_gap, 2) cv_gap
FROM r1e_feat WHERE shape_class LIKE 'sustained%' OR shape_class = 'medium' ORDER BY dur_s DESC LIMIT 10;
