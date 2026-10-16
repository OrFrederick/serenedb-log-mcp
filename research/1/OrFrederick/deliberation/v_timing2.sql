-- T-c alert-label Incidents with full Spread (default tool path)
WITH a AS (SELECT alert_label, node, rack, midplane, epoch(ts) AS t FROM bgl WHERE alert_label <> '-'),
g AS (SELECT *, CASE WHEN t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) <= 300 THEN 0 ELSE 1 END AS is_new FROM a),
s AS (SELECT *, CAST(sum(is_new) OVER (PARTITION BY alert_label ORDER BY t ROWS UNBOUNDED PRECEDING) AS BIGINT) AS inc FROM g)
SELECT count(*), sum(lines) FROM (SELECT alert_label, inc, count(*) lines, count(DISTINCT node) n, count(DISTINCT midplane) m, count(DISTINCT rack) r FROM s GROUP BY 1,2) x;
-- T-d ad-hoc FTS-like filter then Incidents with Spread, key = component
WITH a AS (SELECT component, node, midplane, rack, epoch(ts) t FROM bgl WHERE content ILIKE '%error%'),
g AS (SELECT *, CASE WHEN t - lag(t) OVER (PARTITION BY component ORDER BY t) <= 300 THEN 0 ELSE 1 END is_new FROM a),
s AS (SELECT *, CAST(sum(is_new) OVER (PARTITION BY component ORDER BY t ROWS UNBOUNDED PRECEDING) AS BIGINT) inc FROM g)
SELECT count(*), sum(lines) FROM (SELECT component, inc, count(*) lines, count(DISTINCT node) n, count(DISTINCT midplane) m FROM s GROUP BY 1,2) x;
