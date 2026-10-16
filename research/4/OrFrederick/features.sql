-- Per alert category, measured-only priority features over the whole BGL Dataset (alert lines only).
-- node card = first three parts of node id (e.g. R10-M0-N5); a node-card-hour = one node card active in one clock hour.
WITH a AS (
  SELECT alert_label AS cat, ts, midplane, rack,
         split_part(node,'-',1)||'-'||split_part(node,'-',2)||'-'||split_part(node,'-',3) AS nc,
         (epoch(ts) - epoch(TIMESTAMP '2005-06-03 00:00:00'))::bigint / 604800 AS wk
  FROM bgl WHERE alert_label <> '-'
),
weeks AS (SELECT generate_series(0, 30) AS wk),
cats AS (SELECT DISTINCT cat FROM a),
wk_counts AS (
  SELECT c.cat, w.wk, count(DISTINCT CASE WHEN a.cat IS NOT NULL THEN a.nc || date_trunc('hour', a.ts)::text END) AS nch
  FROM cats c CROSS JOIN weeks w LEFT JOIN a ON a.cat = c.cat AND a.wk = w.wk
  GROUP BY c.cat, w.wk
),
trend AS (
  SELECT cat, regr_slope(nch::double precision, wk::double precision) / NULLIF(avg(nch::double precision),0) AS rel_slope_per_wk
  FROM wk_counts GROUP BY cat
)
SELECT a.cat,
       count(*) AS lines,
       count(DISTINCT a.nc || date_trunc('hour', a.ts)::text) AS nc_hours,
       count(DISTINCT date_trunc('day', a.ts)) AS active_days,
       count(DISTINCT a.wk) AS active_weeks,
       count(DISTINCT a.midplane) AS midplanes,
       count(DISTINCT a.rack) AS racks,
       round(t.rel_slope_per_wk::numeric, 4) AS rel_slope,
       (epoch(TIMESTAMP '2006-01-04 08:00:05') - epoch(max(a.ts)))::bigint / 86400 AS days_since_last,
       count(DISTINCT CASE WHEN a.ts >= TIMESTAMP '2005-12-07 08:00:05' THEN a.nc || date_trunc('hour', a.ts)::text END) AS nc_hours_last28d
FROM a JOIN trend t ON t.cat = a.cat
GROUP BY a.cat, t.rel_slope_per_wk
ORDER BY lines DESC
