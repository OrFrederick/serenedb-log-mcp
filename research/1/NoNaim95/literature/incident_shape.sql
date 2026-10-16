-- Shape of the 1202 Incidents (G=300s): duration, spread classes
WITH a AS (
  SELECT alert_tag, ts, node, line_no,
         CASE WHEN regexp_matches(node, '^R[0-9]{2}-M[01]') THEN substr(node,1,3) END rack,
         CASE WHEN regexp_matches(node, '^R[0-9]{2}-M[01]') THEN substr(node,1,6) END midplane,
         CASE WHEN epoch_us(ts) - lag(epoch_us(ts)) OVER (PARTITION BY alert_tag ORDER BY ts, line_no) < 300000000 THEN 0 ELSE 1 END s
  FROM bgl WHERE alert_tag <> '-'
), b AS (SELECT *, sum(s) OVER (PARTITION BY alert_tag ORDER BY ts, line_no ROWS UNBOUNDED PRECEDING) k FROM a),
c AS (SELECT alert_tag, k, node, count(*) nl FROM b GROUP BY 1,2,3),
i AS (
  SELECT b.alert_tag, b.k, count(*) lines, count(distinct node) nodes, count(distinct midplane) mids, count(distinct rack) racks,
         (epoch_us(max(ts)) - epoch_us(min(ts)))/1e6 dur_s
  FROM b GROUP BY 1,2
), j AS (SELECT i.*, (SELECT max(nl) FROM c WHERE c.alert_tag=i.alert_tag AND c.k=i.k) top FROM i)
SELECT CASE WHEN nodes=1 THEN '1 single node'
            WHEN top*1.0/lines >= 0.9 THEN '2 dominated by one node (>=90%)'
            WHEN mids<=1 THEN '3 one midplane'
            WHEN racks<=1 THEN '4 one rack'
            ELSE '5 multi-rack' END AS spread_class,
       count(*) incidents, sum(lines) lines, max(dur_s) max_dur_s, median(dur_s) med_dur_s,
       sum(CASE WHEN dur_s > 86400 THEN 1 ELSE 0 END) over_24h
FROM j GROUP BY 1 ORDER BY 1;
