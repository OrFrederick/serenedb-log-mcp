-- Team Lead: per month, rts panic lines and how many had a machine check (KERNMC) on the same node in the preceding 120 s
WITH p AS (SELECT line_no, node, ts, epoch(ts) e FROM bgl WHERE alert_label = 'KERNRTSP'),
     mc AS (SELECT node, epoch(ts) e FROM bgl WHERE alert_label = 'KERNMC'),
     hit AS (SELECT DISTINCT p.line_no FROM p JOIN mc ON mc.node = p.node AND mc.e BETWEEN p.e - 120 AND p.e)
SELECT date_trunc('month', p.ts) AS month, count(*) AS rts_panic_lines, count(DISTINCT p.node) AS rts_nodes,
       count(hit.line_no) AS preceded_by_kernmc, round(100.0 * count(hit.line_no) / count(*), 1) AS pct
FROM p LEFT JOIN hit ON hit.line_no = p.line_no GROUP BY 1 ORDER BY 1
