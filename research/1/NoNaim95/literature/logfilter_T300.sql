-- Oliner & Stearley DSN'07 Algorithm 3.1 (LOGFILTER), simultaneous temporal+spatial filter.
-- An alert is redundant iff ANY source reported the same category within the last T seconds
-- (X[c] is updated on every alert, so this is gap-chaining per category, node-agnostic).
-- Keep row iff gap to previous alert of same category >= T (or first). T = 5 s.
WITH a AS (
  SELECT alert_tag, ts,
         epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY ts, line_no) AS gap_us
  FROM bgl WHERE alert_tag <> '-'
)
SELECT alert_tag, count(*) AS raw, sum(CASE WHEN gap_us IS NULL OR gap_us >= 300000000 THEN 1 ELSE 0 END) AS filtered
FROM a GROUP BY alert_tag ORDER BY raw DESC;
