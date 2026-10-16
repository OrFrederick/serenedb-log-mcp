WITH a AS (SELECT node, midplane, epoch(ts) t FROM bgl WHERE content LIKE '%error%'),
g AS (SELECT *, CASE WHEN t - lag(t) OVER (ORDER BY t) <= 300 THEN 0 ELSE 1 END is_new FROM a),
s AS (SELECT *, sum(is_new) OVER (ORDER BY t ROWS UNBOUNDED PRECEDING) inc FROM g)
SELECT inc, count(*) lines, count(DISTINCT node) nodes, count(DISTINCT midplane) mps FROM s GROUP BY 1 ORDER BY 2 DESC LIMIT 5
