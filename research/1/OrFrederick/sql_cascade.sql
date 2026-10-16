WITH a AS (SELECT node, alert_label, epoch(ts) t FROM bgl WHERE alert_label <> '-' AND node LIKE 'R%'),
n AS (SELECT node, alert_label a_lbl, t,
        lead(alert_label) OVER w b_lbl, lead(t) OVER w - t dt
      FROM a WINDOW w AS (PARTITION BY node ORDER BY t))
SELECT a_lbl, b_lbl, count(*) pairs, count(DISTINCT node) nodes, round(median(dt)::numeric,2) med_dt_s
FROM n WHERE b_lbl IS NOT NULL AND b_lbl <> a_lbl AND dt <= 60
GROUP BY 1,2 ORDER BY nodes DESC LIMIT 15
