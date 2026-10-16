-- E1 Engineer: count of lines on the node in the window 03:24:00-03:26:30 by alert category / level
SELECT alert_label, level, count(*) n, min(ts) first_ts, max(ts) last_ts FROM bgl
WHERE node = 'R27-M0-NA-C:J11-U11' AND ts BETWEEN TIMESTAMP '2006-01-03 03:20:00' AND TIMESTAMP '2006-01-03 03:30:00' GROUP BY 1,2 ORDER BY 4;
-- E2 Engineer: lines just after the rts panic on the same midplane (next 10)
SELECT line_no, ts, node, alert_label, level, left(content,80) c FROM bgl
WHERE midplane = 'R27-M0' AND ts > TIMESTAMP '2006-01-03 03:25:05.504039' ORDER BY ts, line_no LIMIT 8;
-- E3 Engineer: history of this node and node card for KERNMC / KERNRTSP
SELECT node, alert_label, count(*), min(ts), max(ts) FROM bgl
WHERE node LIKE 'R27-M0-NA-%' AND alert_label IN ('KERNMC','KERNRTSP') GROUP BY 1,2 ORDER BY 1,2;
-- E4 Team Lead: KERNMC lines by sub-type (content prefix) and whether followed by rts panic within 120 s
WITH mc AS (SELECT line_no, node, epoch(ts) e, regexp_replace(content, '0x[0-9a-f]+', '<hex>', 'g') t FROM bgl WHERE alert_label='KERNMC'),
     p AS (SELECT node, epoch(ts) e FROM bgl WHERE alert_label='KERNRTSP'),
     f AS (SELECT DISTINCT mc.line_no FROM mc JOIN p ON p.node=mc.node AND p.e BETWEEN mc.e AND mc.e+120)
SELECT left(mc.t,75) kernmc_template, count(*) lines, count(f.line_no) followed_by_panic FROM mc LEFT JOIN f ON f.line_no=mc.line_no GROUP BY 1 ORDER BY 2 DESC LIMIT 8;
-- E5 Team Lead: racks with most KERNMC lines; and repeat nodes
SELECT rack, count(*) lines, count(DISTINCT node) nodes FROM bgl WHERE alert_label='KERNMC' GROUP BY 1 ORDER BY 2 DESC LIMIT 5;
SELECT count(*) AS nodes_with_kernmc, count(*) FILTER (WHERE n>1) AS nodes_repeat, max(n) FROM (SELECT node, count(DISTINCT date_trunc('day',ts)) n FROM bgl WHERE alert_label='KERNMC' GROUP BY 1) t
