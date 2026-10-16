WITH a AS (SELECT alert_label, node, midplane, rack, ts, epoch(ts) t FROM bgl WHERE alert_label <> '-'),
g AS (SELECT *, CASE WHEN t - lag(t) OVER (ORDER BY t) <= 300 THEN 0 ELSE 1 END is_new FROM a),
s AS (SELECT *, sum(is_new) OVER (ORDER BY t ROWS UNBOUNDED PRECEDING) ep FROM g)
SELECT ep, min(ts) start_ts, round(max(t)-min(t)) dur_s, count(*) lines, count(DISTINCT alert_label) cats,
  string_agg(DISTINCT alert_label, ',') labels, count(DISTINCT node) nodes, count(DISTINCT midplane) mps, count(DISTINCT rack) racks
FROM s GROUP BY ep ORDER BY mps DESC, lines DESC LIMIT 12
