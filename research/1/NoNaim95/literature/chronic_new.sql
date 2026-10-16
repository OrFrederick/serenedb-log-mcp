-- Chronic vs New per alert category: first_seen, active days, active weeks, incidents (G=300s)
WITH a AS (
  SELECT alert_tag, ts,
         CASE WHEN epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY ts, line_no) < 300000000 THEN 0 ELSE 1 END s
  FROM bgl WHERE alert_tag <> '-'
)
SELECT alert_tag, min(ts) first_seen, max(ts) last_seen, count(*) lines, sum(s) incidents,
       count(distinct date_trunc('day', ts)) active_days, count(distinct date_trunc('week', ts)) active_weeks
FROM a GROUP BY 1 ORDER BY active_days DESC LIMIT 15;
