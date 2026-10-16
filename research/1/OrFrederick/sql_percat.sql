WITH a AS (SELECT alert_label, node, epoch(ts) AS t FROM bgl WHERE alert_label <> '-'),
g AS (SELECT alert_label, node, t,
        t - lag(t) OVER (PARTITION BY alert_label, node ORDER BY t) AS gn,
        t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) AS gc FROM a)
SELECT alert_label, count(*) raw,
  sum(CASE WHEN gn IS NULL OR gn > 5 THEN 1 ELSE 0 END) temporal_5s,
  sum(CASE WHEN gc IS NULL OR gc > 5 THEN 1 ELSE 0 END) inc_5s,
  sum(CASE WHEN gc IS NULL OR gc > 60 THEN 1 ELSE 0 END) inc_60s,
  sum(CASE WHEN gc IS NULL OR gc > 300 THEN 1 ELSE 0 END) inc_5m,
  sum(CASE WHEN gc IS NULL OR gc > 1800 THEN 1 ELSE 0 END) inc_30m,
  sum(CASE WHEN gc IS NULL OR gc > 3600 THEN 1 ELSE 0 END) inc_1h
FROM g GROUP BY 1 ORDER BY raw DESC
