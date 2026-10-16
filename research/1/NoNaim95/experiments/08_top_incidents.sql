-- 08_top_incidents.sql: ranked candidate real Incidents for the worked investigation (#5).
-- BGL: alert Incidents (key alert_tag, G' = 300 s; == Oliner-Stearley filtered alerts) with Spread features.
-- Rank (a judgement call): distinct nodes first (reach), then racks, then entries. Note 1024 nodes x 64 racks = every I/O node
-- of the machine (corpus has exactly 1024 distinct I/O-node names "-I:J18-" and 65,536 compute-chip names "-C:").

SELECT row_number() OVER (ORDER BY nodes DESC, racks DESC, n DESC) rnk, k category, iid,
       make_timestamp_ms((t0 * 1000)::bigint) start_ts, make_timestamp_ms((t1 * 1000)::bigint) end_ts,
       round(dur_s) dur_s, n entries, nodes, nodecards, midplanes, racks, round(top_node_share, 3) top_share,
       spread_class, shape_class
FROM r1e_feat QUALIFY rnk <= 15 ORDER BY rnk;

-- top by entries (includes the one-node spam case)
SELECT row_number() OVER (ORDER BY n DESC) rnk, k category, make_timestamp_ms((t0 * 1000)::bigint) start_ts,
       make_timestamp_ms((t1 * 1000)::bigint) end_ts, round(dur_s) dur_s, n entries, nodes, racks, top_node,
       round(top_node_share, 3) top_share, spread_class
FROM r1e_feat QUALIFY rnk <= 10 ORDER BY rnk;

-- Incident clusters: merge alert Incidents of DIFFERENT categories that overlap/are within 1800 s
-- (cross-category co-occurrence, e.g. KERNSTOR storm 2005-06-13/14). Episode = candidate story for the demo.
DROP TABLE IF EXISTS r1e_episode;
CREATE TABLE r1e_episode AS
WITH f AS (SELECT *, max(t1) OVER (ORDER BY t0, k ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) prev_end FROM r1e_feat),
     g AS (SELECT *, (sum(CASE WHEN prev_end IS NULL OR t0 - prev_end > 1800 THEN 1 ELSE 0 END) OVER (ORDER BY t0, k ROWS UNBOUNDED PRECEDING))::BIGINT eid FROM f)
SELECT eid, min(t0) t0, max(t1) t1, count(*) incidents, count(DISTINCT k) categories, sum(n) entries,
       max(nodes) max_nodes, max(racks) max_racks, string_agg(DISTINCT k, ',') cats
FROM g GROUP BY eid;

SELECT eid, make_timestamp_ms((t0 * 1000)::bigint) start_ts, round((t1 - t0) / 3600, 2) dur_h, incidents, categories,
       entries, max_nodes, max_racks, left(cats, 120) cats
FROM r1e_episode ORDER BY max_racks * 1000000 + entries DESC LIMIT 12;

SELECT count(*) episodes, count(*) FILTER (WHERE categories >= 2) multi_category FROM r1e_episode;

-- OpenStack candidates: the 4 labelled anomaly instances (spawn-time anomaly) and the keystone-503 latency blip
SELECT instance_id, source_file, t_first, t_last, entries, spawn_s, build_s, round(image_to_start_s, 2) img2start_s,
       n_nonINFO, 'labelled anomaly: slow spawn (image creation phase)' AS what
FROM r1e_os_inst WHERE is_anom ORDER BY t_first;

SELECT source_file, ts, status, round(latency_s, 3) latency_s, method, path FROM r1e_os_api
WHERE ts BETWEEN TIMESTAMP '2017-05-17 06:24:30' AND TIMESTAMP '2017-05-17 06:26:30' AND (latency_s > 1 OR status >= 500)
ORDER BY ts;

-- what share of each wide Incident's nodes are I/O nodes (-I:) vs compute chips (-C:)
SELECT f.k, f.iid, f.nodes, count(DISTINCT e.node) FILTER (WHERE e.node LIKE '%-I:%') io_nodes,
       count(DISTINCT e.node) FILTER (WHERE e.node LIKE '%-C:%') compute_chips
FROM r1e_feat f JOIN r1e_ent e ON e.k = f.k AND e.iid = f.iid
WHERE f.nodes >= 1000 GROUP BY f.k, f.iid, f.nodes ORDER BY f.nodes DESC LIMIT 12;
