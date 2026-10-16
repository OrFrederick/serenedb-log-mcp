WITH g AS (SELECT component, node, epoch(ts) t,
   CASE WHEN epoch(ts) - lag(epoch(ts)) OVER (PARTITION BY component ORDER BY ts) <= 300 THEN 0 ELSE 1 END is_new FROM bgl),
s AS (SELECT *, sum(is_new) OVER (PARTITION BY component ORDER BY t ROWS UNBOUNDED PRECEDING) inc FROM g)
SELECT component, count(DISTINCT inc) incidents, count(*) lines FROM s GROUP BY 1 ORDER BY 3 DESC
