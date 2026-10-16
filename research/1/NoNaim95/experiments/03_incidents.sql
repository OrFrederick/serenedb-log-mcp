-- 03_incidents.sql: Incident = merge of Bursts of the same pattern key across nodes.
-- Bursts (per node, gap G) are sorted by start time within (key); a new Incident starts when a Burst
-- starts more than G' seconds after the latest end of all earlier Bursts of that key
-- (gap-based sessionisation at pattern level, ignoring node).
-- Note: if G <= G', this equals entry-level gap sessionisation on the key with gap G' (node ignored),
-- i.e. the Oliner & Stearley filter with T = G' when the key is alert_tag.

DROP TABLE IF EXISTS r1e_bm;
-- Burst -> Incident membership. tag key: all G x G' combinations; mask key: selected combos.
CREATE TABLE r1e_bm AS
WITH x AS (
  SELECT b.*, gp FROM r1e_burst b, (SELECT unnest([5, 60, 300, 3600]) AS gp) p
  WHERE b.kt = 'tag' AND b.g <= p.gp
  UNION ALL
  SELECT b.*, gp FROM r1e_burst b, (SELECT unnest([60, 300, 3600]) AS gp) p
  WHERE b.kt = 'mask' AND b.g = 60 AND b.g <= p.gp),
y AS (
  SELECT *, max(t1) OVER (PARTITION BY kt, g, gp, k ORDER BY t0, node ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prev_end
  FROM x)
SELECT kt, g, gp, k, node, b, t0, t1, n, alert_tag, rack, midplane, nodecard,
       (sum(CASE WHEN prev_end IS NULL OR t0 - prev_end > gp THEN 1 ELSE 0 END)
          OVER (PARTITION BY kt, g, gp, k ORDER BY t0, node ROWS UNBOUNDED PRECEDING))::BIGINT AS iid
FROM y;

DROP TABLE IF EXISTS r1e_inc;
CREATE TABLE r1e_inc AS
SELECT kt, g, gp, k, iid, min(t0) t0, max(t1) t1, sum(n) n, count(*) bursts,
       count(DISTINCT node) nodes, count(DISTINCT rack) racks, count(DISTINCT midplane) midplanes,
       count(DISTINCT nodecard) nodecards,
       sum(CASE WHEN rack IS NULL THEN n ELSE 0 END) nonstd_entries,
       max(n) top_burst_n, arg_max(node, n) top_node, max(alert_tag) alert_tag
FROM r1e_bm GROUP BY kt, g, gp, k, iid;

-- #Incidents per alert category, G=300 bursts, G'=300 (expected to equal Oliner-Stearley filtered counts)
SELECT k AS category, sum(n) entries, count(*) incidents,
       count(*) FILTER (WHERE nodes = 1) single_node, count(*) FILTER (WHERE nodes >= 2) multi_node,
       count(*) FILTER (WHERE racks >= 2) multi_rack, max(nodes) max_nodes, max(racks) max_racks,
       median(nodes) med_nodes, sum(nonstd_entries) nonstd_entries
FROM r1e_inc WHERE kt = 'tag' AND g = 300 AND gp = 300 GROUP BY k ORDER BY entries DESC;

-- totals for every G x G' combination (tag key) and the mask-key combos
SELECT kt, g, gp, count(*) incidents, count(*) FILTER (WHERE nodes = 1) single_node,
       count(*) FILTER (WHERE nodes >= 10) ge10_nodes, count(*) FILTER (WHERE racks >= 2) multi_rack,
       count(*) FILTER (WHERE midplanes >= 2) multi_midplane
FROM r1e_inc GROUP BY kt, g, gp ORDER BY kt, g, gp;

-- Spread distribution for alert Incidents (G=300,G'=300)
SELECT CASE WHEN nodes = 1 THEN '1 node' WHEN nodecards <= 1 THEN '1 nodecard, >1 node'
            WHEN midplanes <= 1 THEN '1 midplane, >1 nodecard' WHEN racks <= 1 THEN '1 rack, 2 midplanes'
            ELSE '>=2 racks' END AS spread_class,
       count(*) incidents, sum(n) entries
FROM r1e_inc WHERE kt = 'tag' AND g = 300 AND gp = 300 GROUP BY 1 ORDER BY 2 DESC;
