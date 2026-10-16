WITH a AS (SELECT alert_label, epoch(ts) t, epoch::double te FROM bgl WHERE alert_label <> '-'),
g AS (SELECT alert_label,
   t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) gt,
   te - lag(te) OVER (PARTITION BY alert_label ORDER BY t) ge FROM a)
SELECT alert_label,
  sum(CASE WHEN gt IS NULL OR gt >  300 THEN 1 ELSE 0 END) ts_gt300,
  sum(CASE WHEN gt IS NULL OR gt >= 300 THEN 1 ELSE 0 END) ts_ge300,
  sum(CASE WHEN ge IS NULL OR ge >  300 THEN 1 ELSE 0 END) ep_gt300,
  sum(CASE WHEN ge IS NULL OR ge >= 300 THEN 1 ELSE 0 END) ep_ge300
FROM g GROUP BY ROLLUP(alert_label) ORDER BY alert_label NULLS FIRST
