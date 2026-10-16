WITH a AS (
  SELECT alert_tag,
         epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY ts, line_no) AS gus,
         epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY line_no) AS gline
  FROM bgl WHERE alert_tag <> '-'
)
SELECT t_s,
  sum(CASE WHEN gus IS NULL OR gus >= t_s*1000000 THEN 1 ELSE 0 END) time_order,
  sum(CASE WHEN gline IS NULL OR gline >= t_s*1000000 THEN 1 ELSE 0 END) line_order
FROM a, (SELECT unnest([1,5,10,30,60,120,300,600,1800,3600])::BIGINT AS t_s) p
GROUP BY t_s ORDER BY t_s;
