DROP TABLE IF EXISTS r1_bgl_incidents;
CREATE TABLE r1_bgl_incidents AS
WITH a AS (
  SELECT alert_label, node, rack, midplane, ts, epoch(ts) AS t,
         CASE WHEN node ~ '^R[0-9A-F]{2}-M[01]-N[0-9A-F]' THEN left(node, 10) END AS nodecard
  FROM bgl WHERE alert_label <> '-'
), g AS (
  SELECT *, CASE WHEN t - lag(t) OVER (PARTITION BY alert_label ORDER BY t) <= 300 THEN 0 ELSE 1 END AS is_new
  FROM a
), s AS (
  SELECT *, sum(is_new) OVER (PARTITION BY alert_label ORDER BY t ROWS UNBOUNDED PRECEDING) AS inc_seq FROM g
)
SELECT alert_label, inc_seq, min(ts) AS start_ts, max(ts) AS end_ts,
       max(t) - min(t) AS duration_s, count(*) AS lines,
       count(DISTINCT node) AS nodes, count(DISTINCT nodecard) AS nodecards,
       count(DISTINCT midplane) AS midplanes, count(DISTINCT rack) AS racks,
       min(node) AS first_node
FROM s GROUP BY alert_label, inc_seq
