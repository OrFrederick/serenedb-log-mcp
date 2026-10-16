-- 05b_cascade.sql: (c) cascade = Burst of pattern A on a node followed by a Burst of pattern B (B != A)
-- starting on the SAME node within W seconds after A's start. Bursts at G = 300 s.
-- support(A->B) = #A-Bursts followed by >=1 B-Burst;  conf = support / #A-Bursts;
-- baseline(B)  = share of all Bursts (of any pattern != B, on located nodes) followed by a B-Burst within W;
-- lift = conf / baseline(B).  Only located nodes (rack parsed) are used.

-- alert_tag patterns, W = 3600 s
DROP TABLE IF EXISTS r1e_casc_tag;
CREATE TABLE r1e_casc_tag AS
WITH b AS (SELECT node, k, t0 FROM r1e_burst WHERE kt = 'tag' AND g = 300 AND rack IS NOT NULL),
     p AS (SELECT DISTINCT a.k ka, c.k kb, a.node, a.t0 FROM b a JOIN b c
             ON a.node = c.node AND c.t0 > a.t0 AND c.t0 <= a.t0 + 3600 AND c.k <> a.k),
     na AS (SELECT k, count(*) n FROM b GROUP BY k),
     tot AS (SELECT count(*) n FROM b),
     base AS (SELECT kb, count(*) followed FROM p GROUP BY kb),
     s AS (SELECT ka, kb, count(*) support, count(DISTINCT node) nodes FROM p GROUP BY ka, kb)
SELECT s.ka, s.kb, s.support, s.nodes, na.n AS a_bursts, (s.support::double) / na.n AS conf,
       (base.followed::double) / (tot.n - nb.n) AS baseline,
       ((s.support::double) / na.n) / ((base.followed::double) / (tot.n - nb.n)) AS lift
FROM s JOIN na ON na.k = s.ka JOIN na nb ON nb.k = s.kb JOIN base ON base.kb = s.kb, tot;

SELECT ka, kb, support, nodes, a_bursts, round(conf, 3) conf, round(baseline, 5) baseline, round(lift, 1) lift
FROM r1e_casc_tag WHERE support >= 20 ORDER BY support DESC LIMIT 20;

-- masked-message patterns, W = 60 s (finer: catches kernel-crash -> core-dump chains in non-alert lines)
DROP TABLE IF EXISTS r1e_casc_mask;
CREATE TABLE r1e_casc_mask AS
WITH b AS (SELECT node, k, t0 FROM r1e_burst WHERE kt = 'mask' AND g = 300 AND rack IS NOT NULL),
     p AS (SELECT DISTINCT a.k ka, c.k kb, a.node, a.t0 FROM b a JOIN b c
             ON a.node = c.node AND c.t0 > a.t0 AND c.t0 <= a.t0 + 60 AND c.k <> a.k),
     na AS (SELECT k, count(*) n FROM b GROUP BY k),
     tot AS (SELECT count(*) n FROM b),
     base AS (SELECT kb, count(*) followed FROM p GROUP BY kb),
     s AS (SELECT ka, kb, count(*) support, count(DISTINCT node) nodes FROM p GROUP BY ka, kb)
SELECT s.ka, s.kb, s.support, s.nodes, na.n AS a_bursts, (s.support::double) / na.n AS conf,
       (base.followed::double) / (tot.n - nb.n) AS baseline,
       ((s.support::double) / na.n) / ((base.followed::double) / (tot.n - nb.n)) AS lift
FROM s JOIN na ON na.k = s.ka JOIN na nb ON nb.k = s.kb JOIN base ON base.kb = s.kb, tot;

-- top pairs by support with high confidence and lift (support >= 50 nodes-bursts, conf >= 0.5, lift >= 10)
SELECT left(ka, 70) ka, left(kb, 70) kb, support, nodes, a_bursts, round(conf, 3) conf, round(lift, 1) lift
FROM r1e_casc_mask WHERE support >= 50 AND conf >= 0.5 AND lift >= 10 ORDER BY support DESC LIMIT 25;

-- pairs where A is an alert pattern (an alert followed by something else on the same node)
SELECT left(c.ka, 60) ka, left(c.kb, 70) kb, c.support, c.nodes, c.a_bursts, round(c.conf, 3) conf, round(c.lift, 1) lift
FROM r1e_casc_mask c
WHERE c.ka IN (SELECT DISTINCT pkey FROM r1e_bgl WHERE alert_tag <> '-') AND c.support >= 20 AND c.lift >= 5
ORDER BY c.support DESC LIMIT 20;

SELECT count(*) pairs, count(*) FILTER (WHERE support >= 50 AND conf >= 0.5 AND lift >= 10) strong_pairs FROM r1e_casc_mask;
