-- V1 Oliner filter re-check at T=5 and T=300 (any-source, per category)
WITH a AS (SELECT alert_label, epoch(ts) t, lag(epoch(ts)) OVER (PARTITION BY alert_label ORDER BY ts) p FROM bgl WHERE alert_label <> '-')
SELECT count(*) FILTER (WHERE p IS NULL OR t-p > 5) t5, count(*) FILTER (WHERE p IS NULL OR t-p > 300) t300 FROM a;
-- V3 type of window sum
SELECT pg_typeof(sum(1) OVER ()) FROM bgl LIMIT 1;
-- V4 ICDM'08 extra alert types tagged '-'
SELECT alert_label, CASE WHEN content LIKE '%Unable to steer%' THEN 'steer' ELSE 'softfail' END k, count(*), count(DISTINCT node) FROM bgl WHERE content LIKE '%Unable to steer%' OR content ILIKE '%excessive soft failures%' GROUP BY 1,2;
-- V5 correctable-error chatter stop
SELECT 'ddr' k, max(ts) FROM bgl WHERE content LIKE 'ddr:%' UNION ALL SELECT 'torus', max(ts) FROM bgl WHERE content ILIKE '%torus%' UNION ALL SELECT 'edram', max(ts) FROM bgl WHERE content ILIKE '%edram%' UNION ALL SELECT 'corpus_end', max(ts) FROM bgl;
-- V6 epoch vs ts offset
SELECT epoch, ts, epoch(ts) - epoch AS offset_s FROM bgl WHERE line_no IN (1, 2000000, 4747963);
-- V8 KERNTERM -> KERNREC on 2005-11-15 19:18: per-node delay
WITH t AS (SELECT node, min(epoch(ts)) tt FROM bgl WHERE alert_label='KERNTERM' AND ts BETWEEN '2005-11-15 19:15' AND '2005-11-15 19:25' GROUP BY 1),
     r AS (SELECT node, min(epoch(ts)) tr FROM bgl WHERE alert_label='KERNREC' AND ts BETWEEN '2005-11-15 19:15' AND '2005-11-15 19:30' GROUP BY 1)
SELECT count(*) nodes, min(tr-tt) min_d, median(tr-tt) med_d, max(tr-tt) max_d, min(to_timestamp(tt)) first_term, max(to_timestamp(tt)) last_term FROM t JOIN r USING(node);
