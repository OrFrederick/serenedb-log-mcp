WITH a AS (SELECT source_file, component, level, epoch(ts) t, ts,
   regexp_replace(regexp_replace(content, '[0-9a-f]{8}-[0-9a-f-]{27}', '<UUID>', 'g'), '[0-9a-f]{32}|[0-9]+(\.[0-9]+)?', '<N>', 'g') tpl
  FROM openstack WHERE level <> 'INFO'),
g AS (SELECT *, CASE WHEN t - lag(t) OVER (PARTITION BY source_file, tpl ORDER BY t) <= 300 THEN 0 ELSE 1 END is_new FROM a),
s AS (SELECT *, sum(is_new) OVER (PARTITION BY source_file, tpl ORDER BY t ROWS UNBOUNDED PRECEDING) inc FROM g)
SELECT source_file, level, left(tpl, 90) tpl, count(DISTINCT inc) incidents, count(*) lines, min(ts) first_ts
FROM s GROUP BY 1,2,3 ORDER BY lines DESC
