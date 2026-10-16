WITH a AS (
  SELECT alert_tag,
         epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY ts, line_no) AS gus,
         epoch - lag(epoch) OVER (PARTITION BY alert_tag ORDER BY epoch, line_no) AS gs
  FROM bgl WHERE alert_tag <> '-'
)
SELECT alert_tag, count(*) raw,
  sum(CASE WHEN gus IS NULL OR gus >= 5000000 THEN 1 ELSE 0 END) us_ge5,
  sum(CASE WHEN gus IS NULL OR gus >  5000000 THEN 1 ELSE 0 END) us_gt5,
  sum(CASE WHEN gs  IS NULL OR gs  >= 5 THEN 1 ELSE 0 END) s_ge5,
  sum(CASE WHEN gs  IS NULL OR gs  >  5 THEN 1 ELSE 0 END) s_gt5
FROM a GROUP BY ROLLUP(alert_tag) ORDER BY raw DESC LIMIT 12;
