-- T-a (our Research query): Incidents keyed by Component over all rows, count only
WITH g AS (SELECT component, epoch(ts) t, CASE WHEN epoch(ts) - lag(epoch(ts)) OVER (PARTITION BY component ORDER BY ts) <= 300 THEN 0 ELSE 1 END is_new FROM bgl),
s AS (SELECT *, sum(is_new) OVER (PARTITION BY component ORDER BY t ROWS UNBOUNDED PRECEDING) inc FROM g)
SELECT component, count(DISTINCT inc) incidents, count(*) lines FROM s GROUP BY 1 ORDER BY 3 DESC;
-- T-b (what a list_incidents tool returns): one row per Incident with Spread, top 20 by lines
WITH g AS (SELECT component, node, midplane, rack, epoch(ts) t, CASE WHEN epoch(ts) - lag(epoch(ts)) OVER (PARTITION BY component ORDER BY ts) <= 300 THEN 0 ELSE 1 END is_new FROM bgl),
s AS (SELECT *, CAST(sum(is_new) OVER (PARTITION BY component ORDER BY t ROWS UNBOUNDED PRECEDING) AS BIGINT) inc FROM g),
i AS (SELECT component, inc, min(t) t0, max(t)-min(t) dur, count(*) lines, count(DISTINCT node) nodes, count(DISTINCT midplane) mps, count(DISTINCT rack) racks FROM s GROUP BY 1,2)
SELECT (SELECT count(*) FROM i) n_incidents, component, inc, to_timestamp(t0) start, dur, lines, nodes, mps, racks FROM i ORDER BY lines DESC LIMIT 20;
