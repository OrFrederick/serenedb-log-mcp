WITH a AS (
  SELECT alert_label, node, epoch(ts) AS t FROM bgl WHERE alert_label <> '-'
), g AS (
  SELECT alert_label, node, t,
         t - lag(t) OVER (PARTITION BY alert_label, node ORDER BY t) AS gap_node,
         t - lag(t) OVER (PARTITION BY alert_label ORDER BY t)       AS gap_cat
  FROM a
), th AS (SELECT unnest(ARRAY[5, 60, 300, 1800, 3600, 21600]) AS thr)
SELECT thr,
       sum(CASE WHEN gap_node IS NULL OR gap_node > thr THEN 1 ELSE 0 END) AS bursts_node,
       sum(CASE WHEN gap_cat  IS NULL OR gap_cat  > thr THEN 1 ELSE 0 END) AS incidents_cat
FROM g CROSS JOIN th GROUP BY thr ORDER BY thr
