-- 02_bursts.sql: Burst = maximal run of Log Entries with the same (node, pattern key) where each
-- consecutive gap <= G seconds. A new Burst starts when gap to previous entry of that (node,key) > G.
-- Two keyings: kt='tag' (alert_tag, alert rows only) and kt='mask' (masked message key, all rows).

-- per-entry gap to previous entry of same (node,key); one table holds both keyings
DROP TABLE IF EXISTS r1e_gap;
CREATE TABLE r1e_gap AS
SELECT 'tag' AS kt, line_no, t, coalesce(node, '<null>') AS node, alert_tag AS k, alert_tag,
       rack, midplane, nodecard,
       t - lag(t) OVER (PARTITION BY coalesce(node, '<null>'), alert_tag ORDER BY t, line_no) AS gap
FROM r1e_bgl WHERE alert_tag <> '-';
INSERT INTO r1e_gap
SELECT 'mask', line_no, t, coalesce(node, '<null>'), pkey, alert_tag, rack, midplane, nodecard,
       t - lag(t) OVER (PARTITION BY coalesce(node, '<null>'), pkey ORDER BY t, line_no)
FROM r1e_bgl;

-- burst ids for each G in one pass (cumulative sum of "is start" flags)
-- GOTCHA: sum() returns HUGEINT; GROUP BY on these HUGEINT ids crashed SereneDB (SIGSEGV) twice -> cast to BIGINT.
DROP TABLE IF EXISTS r1e_bid;
CREATE TABLE r1e_bid AS
SELECT kt, line_no, t, node, k, alert_tag, rack, midplane, nodecard,
       (sum(CASE WHEN gap IS NULL OR gap > 5    THEN 1 ELSE 0 END) OVER w)::BIGINT AS b5,
       (sum(CASE WHEN gap IS NULL OR gap > 60   THEN 1 ELSE 0 END) OVER w)::BIGINT AS b60,
       (sum(CASE WHEN gap IS NULL OR gap > 300  THEN 1 ELSE 0 END) OVER w)::BIGINT AS b300,
       (sum(CASE WHEN gap IS NULL OR gap > 3600 THEN 1 ELSE 0 END) OVER w)::BIGINT AS b3600
FROM r1e_gap
WINDOW w AS (PARTITION BY kt, node, k ORDER BY t, line_no ROWS UNBOUNDED PRECEDING);

-- materialise Bursts for each G (one INSERT per G to keep memory low on the shared server)
DROP TABLE IF EXISTS r1e_burst;
CREATE TABLE r1e_burst AS
SELECT kt, 5 AS g, node, k, b5 AS b, min(t) t0, max(t) t1, count(*) n, max(alert_tag) alert_tag,
       max(rack) rack, max(midplane) midplane, max(nodecard) nodecard
FROM r1e_bid GROUP BY kt, node, k, b5;
INSERT INTO r1e_burst SELECT kt, 60, node, k, b60, min(t), max(t), count(*), max(alert_tag), max(rack), max(midplane), max(nodecard) FROM r1e_bid GROUP BY kt, node, k, b60;
INSERT INTO r1e_burst SELECT kt, 300, node, k, b300, min(t), max(t), count(*), max(alert_tag), max(rack), max(midplane), max(nodecard) FROM r1e_bid GROUP BY kt, node, k, b300;
INSERT INTO r1e_burst SELECT kt, 3600, node, k, b3600, min(t), max(t), count(*), max(alert_tag), max(rack), max(midplane), max(nodecard) FROM r1e_bid GROUP BY kt, node, k, b3600;

-- #Bursts and entries-per-Burst distribution per G
SELECT kt, g, count(*) bursts, sum(n) entries, median(n) med, quantile_cont(n, 0.9) p90,
       quantile_cont(n, 0.99) p99, max(n) mx, count(*) FILTER (WHERE n = 1) singleton_bursts,
       count(*) FILTER (WHERE n >= 1000) bursts_ge_1000, median(t1 - t0) med_dur_s
FROM r1e_burst GROUP BY kt, g ORDER BY kt, g;

-- top "one node spamming" Bursts (G=300, masked key)
SELECT node, k, n, make_timestamp_ms((t0 * 1000)::bigint) start_ts, round(t1 - t0) dur_s,
       round(n / greatest(t1 - t0, 1), 2) per_s, alert_tag
FROM r1e_burst WHERE kt = 'mask' AND g = 300 ORDER BY n DESC LIMIT 15;

-- top single-node alert Bursts (G=300, alert_tag key)
SELECT node, k, n, make_timestamp_ms((t0 * 1000)::bigint) start_ts, round(t1 - t0) dur_s
FROM r1e_burst WHERE kt = 'tag' AND g = 300 ORDER BY n DESC LIMIT 15;

-- whole-period per-node domination: share of a key's entries from its single loudest node
WITH nk AS (SELECT k, node, count(*) n FROM r1e_gap WHERE kt = 'tag' GROUP BY k, node),
     a AS (SELECT k, sum(n) tot, max(n) top, arg_max(node, n) top_node, count(*) nodes FROM nk GROUP BY k)
SELECT k, tot, nodes, top_node, top, round(top::double / tot, 3) top_share FROM a ORDER BY tot DESC LIMIT 15;
