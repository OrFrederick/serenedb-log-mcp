-- Liang et al. DSN'05 Step II analogue: temporal compression at a single location
-- Burst = maximal run of alerts with same (node, alert_tag) whose consecutive gaps < G
WITH a AS (
  SELECT epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY node, alert_tag ORDER BY ts, line_no) AS g
  FROM bgl WHERE alert_tag <> '-'
)
SELECT t_s AS gap_s, sum(CASE WHEN g IS NULL OR g >= t_s*1000000 THEN 1 ELSE 0 END) AS node_bursts
FROM a, (SELECT unnest([0,1,5,30,60,120,300,1200,3600])::BIGINT AS t_s) p
GROUP BY t_s ORDER BY t_s;
