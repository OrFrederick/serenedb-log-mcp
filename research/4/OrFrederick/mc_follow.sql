-- Of all machine check (KERNMC) lines, how many are followed by an rts panic on the same node within 120 s?
WITH mc AS (SELECT line_no, node, epoch(ts) e, content FROM bgl WHERE alert_label = 'KERNMC'),
     p  AS (SELECT node, epoch(ts) e FROM bgl WHERE alert_label = 'KERNRTSP'),
     f  AS (SELECT DISTINCT mc.line_no FROM mc JOIN p ON p.node = mc.node AND p.e BETWEEN mc.e AND mc.e + 120)
SELECT (f.line_no IS NOT NULL) AS followed_by_rts_panic_120s, count(*) AS kernmc_lines, count(DISTINCT mc.node) AS nodes
FROM mc LEFT JOIN f ON f.line_no = mc.line_no GROUP BY 1;
-- weekly rts panic lines and distinct nodes (trend for Team Lead)
SELECT date_trunc('month', ts) AS month, count(*) FILTER (WHERE alert_label='KERNRTSP') AS rts_lines,
       count(DISTINCT CASE WHEN alert_label='KERNRTSP' THEN node END) AS rts_nodes,
       count(*) FILTER (WHERE alert_label='KERNMC') AS mc_lines,
       count(DISTINCT CASE WHEN alert_label='KERNMC' THEN node END) AS mc_nodes
FROM bgl WHERE alert_label IN ('KERNRTSP','KERNMC') GROUP BY 1 ORDER BY 1;
-- non-alert content on the same node in the 120 s before an rts panic that has no preceding alert
WITH p AS (SELECT node, epoch(ts) e FROM bgl WHERE alert_label = 'KERNRTSP')
SELECT left(regexp_replace(b.content, '[0-9a-fx]{4,}', '<*>', 'g'), 60) AS tmpl, count(*) n, count(DISTINCT b.node) nodes
FROM p JOIN bgl b ON b.node = p.node AND epoch(b.ts) BETWEEN p.e - 120 AND p.e - 0.001 AND b.alert_label = '-'
GROUP BY 1 ORDER BY 2 DESC LIMIT 10
