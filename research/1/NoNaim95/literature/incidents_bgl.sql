-- Incident (category-level, node-agnostic gap chaining, G = 300 s) with Spread
WITH a AS (
  SELECT alert_tag, ts, node,
         CASE WHEN regexp_matches(node, '^R[0-9]{2}-M[01]') THEN substr(node,1,3) END AS rack,
         CASE WHEN regexp_matches(node, '^R[0-9]{2}-M[01]') THEN substr(node,1,6) END AS midplane,
         CASE WHEN regexp_matches(node, '^R[0-9]{2}-M[01]-N[0-9A-F]') THEN substr(node,1,9) END AS nodecard,
         CASE WHEN epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY ts, line_no) < 300000000 THEN 0 ELSE 1 END AS is_start
  FROM bgl WHERE alert_tag <> '-'
), b AS (
  SELECT *, sum(is_start) OVER (PARTITION BY alert_tag ORDER BY ts ROWS UNBOUNDED PRECEDING) AS inc_no FROM a
), i AS (
  SELECT alert_tag, inc_no, min(ts) t0, max(ts) t1, count(*) lines,
         count(distinct node) nodes, count(distinct nodecard) nodecards, count(distinct midplane) midplanes, count(distinct rack) racks,
         max(cnt_node) AS top_node_lines
  FROM (SELECT *, count(*) OVER (PARTITION BY alert_tag, inc_no, node) cnt_node FROM b) x
  GROUP BY alert_tag, inc_no
)
SELECT * FROM i ORDER BY lines DESC LIMIT 25;
