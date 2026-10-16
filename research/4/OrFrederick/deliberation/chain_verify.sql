-- (1) APPSEV in the window after the rts panic on R27-M0-NA-C:J11-U11, 2006-01-03
SELECT count(*) AS lines, count(DISTINCT node) AS nodes,
       count(DISTINCT node) FILTER (WHERE midplane = 'R27-M0') AS nodes_on_r27m0,
       min(ts), max(ts)
FROM bgl WHERE alert_label = 'APPSEV'
  AND ts BETWEEN TIMESTAMP '2006-01-03 03:25:05' AND TIMESTAMP '2006-01-03 03:28:00';
-- (2) APPSEV Incident starts (gap 300 s, all nodes) preceded by any KERNMC within 120 s, vs hourly probe baseline
WITH a AS (SELECT ts, lag(ts) OVER (ORDER BY ts) AS p FROM bgl WHERE alert_label = 'APPSEV'),
starts AS (SELECT ts FROM a WHERE p IS NULL OR epoch(ts) - epoch(p) >= 300),
mc AS (SELECT ts FROM bgl WHERE alert_label = 'KERNMC'),
probes AS (SELECT generate_series(TIMESTAMP '2005-06-03 16:00:00', TIMESTAMP '2006-01-04 07:00:00', INTERVAL 1 HOUR) AS ts)
SELECT 'appsev_incident_starts' AS what, count(*) AS n,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM mc WHERE mc.ts BETWEEN s.ts - INTERVAL 120 SECOND AND s.ts)) AS after_mc
FROM starts s
UNION ALL
SELECT 'hourly_probes', count(*),
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM mc WHERE mc.ts BETWEEN pr.ts - INTERVAL 120 SECOND AND pr.ts))
FROM probes pr;
