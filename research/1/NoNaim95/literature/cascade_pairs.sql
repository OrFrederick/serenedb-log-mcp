-- Cascade candidates: on the same node, the next alert of a DIFFERENT category within W=60 s.
-- Collapse each (node, category) Burst (gap<300s) to its first line first, so spam does not dominate.
WITH a AS (
  SELECT node, alert_tag, ts,
         epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY node, alert_tag ORDER BY ts, line_no) g
  FROM bgl WHERE alert_tag <> '-'
), heads AS (SELECT node, alert_tag, ts FROM a WHERE g IS NULL OR g >= 300000000),
n AS (
  SELECT node, alert_tag a_tag, ts a_ts,
         lead(alert_tag) OVER (PARTITION BY node ORDER BY ts) b_tag,
         lead(ts) OVER (PARTITION BY node ORDER BY ts) b_ts
  FROM heads
)
SELECT a_tag, b_tag, count(*) node_pairs, count(distinct date_trunc('day', a_ts)) days,
       median(epoch(b_ts) - epoch(a_ts)) med_lag_s
FROM n WHERE b_tag <> a_tag AND epoch(b_ts) - epoch(a_ts) <= 60
GROUP BY 1,2 ORDER BY node_pairs DESC LIMIT 15;
