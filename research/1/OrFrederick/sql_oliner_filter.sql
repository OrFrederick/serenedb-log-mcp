-- Two-stage filter in the style of Oliner & Stearley 2007 (T = 5 s):
-- stage 1 temporal: drop an alert if the same node reported the same category within T before it;
-- stage 2 spatial: among survivors, drop an alert if any node's surviving alert of the same category came within T before it.
WITH a AS (SELECT alert_label, node, epoch(ts) t FROM bgl WHERE alert_label <> '-'),
t1 AS (SELECT *, t - lag(t) OVER (PARTITION BY alert_label, node ORDER BY t) g FROM a),
s1 AS (SELECT alert_label, node, t FROM t1 WHERE g IS NULL OR g > 5),
t2 AS (SELECT *, t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) g2 FROM s1)
SELECT alert_label, (SELECT count(*) FROM a x WHERE x.alert_label = t2.alert_label) raw,
       count(*) after_temporal, sum(CASE WHEN g2 IS NULL OR g2 > 5 THEN 1 ELSE 0 END) after_spatial
FROM t2 GROUP BY alert_label ORDER BY raw DESC
