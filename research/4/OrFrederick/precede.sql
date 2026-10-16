-- For each rts panic (KERNRTSP) line: which alert category, if any, appeared on the SAME node in the preceding 120 s?
WITH p AS (SELECT line_no, node, epoch(ts) e FROM bgl WHERE alert_label = 'KERNRTSP'),
     o AS (SELECT node, epoch(ts) e, alert_label FROM bgl WHERE alert_label NOT IN ('-', 'KERNRTSP')),
     m AS (SELECT p.line_no, min(p.e - o.e) AS gap_s, string_agg(DISTINCT o.alert_label, ',') AS cats
           FROM p JOIN o ON o.node = p.node AND o.e BETWEEN p.e - 120 AND p.e GROUP BY p.line_no)
SELECT coalesce(m.cats, '(none within 120 s)') AS preceding_alert_cats, count(*) AS rts_panic_lines,
       round(avg(m.gap_s)::numeric, 1) AS avg_gap_s
FROM p LEFT JOIN m ON m.line_no = p.line_no GROUP BY 1 ORDER BY 2 DESC
