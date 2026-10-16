-- 07_sensitivity.sql: how #Incidents changes with G (Burst gap), G' (Incident merge gap) and spread thresholds.

-- (1) alert_tag key, finer G' sweep: entry-level sessionisation on category (equivalent to Bursts with any G <= G')
WITH a AS (SELECT alert_tag k, t - lag(t) OVER (PARTITION BY alert_tag ORDER BY t, line_no) gap FROM r1e_bgl WHERE alert_tag <> '-')
SELECT gp, count(*) FILTER (WHERE gap IS NULL OR gap > gp) incidents,
       count(*) FILTER (WHERE (gap IS NULL OR gap > gp) AND k = 'KERNDTLB') kerndtlb,
       count(*) FILTER (WHERE (gap IS NULL OR gap > gp) AND k = 'KERNSTOR') kernstor,
       count(*) FILTER (WHERE (gap IS NULL OR gap > gp) AND k = 'APPSEV') appsev,
       count(*) FILTER (WHERE (gap IS NULL OR gap > gp) AND k = 'KERNTERM') kernterm,
       count(*) FILTER (WHERE (gap IS NULL OR gap > gp) AND k = 'KERNRTSP') kernrtsp
FROM a, (SELECT unnest([1, 5, 30, 60, 120, 300, 600, 1800, 3600, 21600, 86400]) gp) x GROUP BY gp ORDER BY gp;

-- (2) Burst counts (per node) vs G for both keys (from 02), and Incident counts for all computed G x G'
SELECT kt, g, count(*) bursts, count(*) FILTER (WHERE n >= 100) bursts_ge100 FROM r1e_burst GROUP BY kt, g ORDER BY kt, g;
SELECT kt, g, gp, count(*) incidents, round(avg(nodes), 1) avg_nodes, max(n) max_entries FROM r1e_inc GROUP BY kt, g, gp ORDER BY kt, g, gp;

-- (3) spread thresholds: #Incidents at or above each threshold (tag key, G=G'=300 and G'=3600)
SELECT gp, count(*) total,
       count(*) FILTER (WHERE nodes >= 2) n_ge2, count(*) FILTER (WHERE nodes >= 10) n_ge10,
       count(*) FILTER (WHERE nodes >= 100) n_ge100, count(*) FILTER (WHERE nodes >= 1000) n_ge1000,
       count(*) FILTER (WHERE midplanes >= 2) mp_ge2, count(*) FILTER (WHERE racks >= 2) r_ge2,
       count(*) FILTER (WHERE racks >= 8) r_ge8, count(*) FILTER (WHERE racks >= 32) r_ge32
FROM r1e_inc WHERE kt = 'tag' AND g = 300 GROUP BY gp ORDER BY gp;

-- (4) same for masked-key Incidents (G=60), to show the effect of the pattern key
SELECT gp, count(*) total, count(*) FILTER (WHERE nodes >= 2) n_ge2, count(*) FILTER (WHERE nodes >= 10) n_ge10,
       count(*) FILTER (WHERE nodes >= 100) n_ge100, count(*) FILTER (WHERE racks >= 2) r_ge2, count(*) FILTER (WHERE racks >= 8) r_ge8
FROM r1e_inc WHERE kt = 'mask' GROUP BY gp ORDER BY gp;

-- (5) node-dominance threshold sensitivity (share of entries from the loudest node), tag key G'=300
SELECT thr, count(*) FILTER (WHERE top_node_share >= thr) incidents_dominated,
       sum(n) FILTER (WHERE top_node_share >= thr) entries_in_dominated
FROM r1e_feat, (SELECT unnest([0.5, 0.8, 0.9, 0.99, 1.0]) thr) x GROUP BY thr ORDER BY thr;

-- (6) per-category Incident counts across G' (tag key), top categories
SELECT k, sum(n) FILTER (WHERE gp = 300) entries,
       count(*) FILTER (WHERE gp = 5) gp5, count(*) FILTER (WHERE gp = 60) gp60,
       count(*) FILTER (WHERE gp = 300) gp300, count(*) FILTER (WHERE gp = 3600) gp3600
FROM r1e_inc WHERE kt = 'tag' AND g = 5 GROUP BY k ORDER BY entries DESC LIMIT 15;
