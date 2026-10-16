WITH a AS (SELECT alert_label, node, coalesce(midplane,'?') mp, coalesce(rack,'?') rk, epoch(ts) t FROM bgl WHERE alert_label <> '-'),
g AS (SELECT
  t - lag(t) OVER (PARTITION BY alert_label, node ORDER BY t) g_node,
  t - lag(t) OVER (PARTITION BY alert_label, mp ORDER BY t) g_mp,
  t - lag(t) OVER (PARTITION BY alert_label, rk ORDER BY t) g_rk,
  t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) g_cat,
  t - lag(t) OVER (ORDER BY t) g_all FROM a),
th AS (SELECT unnest(ARRAY[5,60,300,1800,3600]) thr)
SELECT thr,
 sum(CASE WHEN g_node IS NULL OR g_node>thr THEN 1 ELSE 0 END) per_node,
 sum(CASE WHEN g_mp IS NULL OR g_mp>thr THEN 1 ELSE 0 END) per_midplane,
 sum(CASE WHEN g_rk IS NULL OR g_rk>thr THEN 1 ELSE 0 END) per_rack,
 sum(CASE WHEN g_cat IS NULL OR g_cat>thr THEN 1 ELSE 0 END) per_category,
 sum(CASE WHEN g_all IS NULL OR g_all>thr THEN 1 ELSE 0 END) any_category
FROM g CROSS JOIN th GROUP BY thr ORDER BY thr
