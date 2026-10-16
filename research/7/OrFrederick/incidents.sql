-- Incidents per #1 (key = alert_label, merged across nodes, chained gap 300 s), with masked messages per Incident.
WITH a AS (SELECT alert_label, node, midplane, rack, component, ts, epoch(ts) AS t,
                  regexp_replace(content, '[0-9a-fA-Fx]*[0-9][0-9a-fA-Fx]*', '#', 'g') AS msg
           FROM bgl WHERE alert_label <> '-'),
g AS (SELECT *, CASE WHEN t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) <= 300 THEN 0 ELSE 1 END AS is_new FROM a),
s AS (SELECT *, sum(is_new) OVER (PARTITION BY alert_label ORDER BY t ROWS UNBOUNDED PRECEDING) AS inc FROM g),
i AS (SELECT alert_label, inc, min(t) AS start_t, max(t) - min(t) AS duration_s, count(*) AS lines,
             count(DISTINCT node) AS nodes, count(DISTINCT midplane) AS midplanes, count(DISTINCT rack) AS racks
      FROM s GROUP BY alert_label, inc),
m AS (SELECT alert_label, inc, component, msg, count(*) AS n FROM s GROUP BY alert_label, inc, component, msg)
SELECT i.alert_label, i.inc, i.start_t, i.duration_s, i.lines, i.nodes, i.midplanes, i.racks, m.component, m.msg, m.n
FROM i JOIN m ON i.alert_label = m.alert_label AND i.inc = m.inc
ORDER BY i.start_t, m.n DESC
