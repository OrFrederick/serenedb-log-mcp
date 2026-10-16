-- V8b KERNREC wave start vs KERNTERM wave end, 2005-11-15
SELECT alert_label, min(ts), max(ts), count(*) FROM bgl WHERE alert_label IN ('KERNTERM','KERNREC') AND ts BETWEEN '2005-11-15 19:15' AND '2005-11-15 19:30' GROUP BY 1;
-- V5b correctable-error chatter: last occurrence per masked message, those with >=1000 lines
SELECT left(regexp_replace(content,'[0-9]+','N','g'),60) m, count(*) n, min(ts) first_ts, max(ts) last_ts FROM bgl
WHERE content ILIKE '%correctable%' OR content ILIKE 'ddr:%' OR content ILIKE '%torus%' OR content ILIKE '%edram%' OR content ILIKE '%CE sym%'
GROUP BY 1 HAVING count(*) >= 1000 ORDER BY n DESC LIMIT 15;
-- V5c weekly line count of those messages around 2005-11-21
SELECT date_trunc('week', ts) wk, count(*) FROM bgl WHERE (content ILIKE '%correctable%' OR content ILIKE 'ddr:%' OR content ILIKE '%torus%' OR content ILIKE '%edram%' OR content ILIKE '%CE sym%') AND ts BETWEEN '2005-10-24' AND '2006-01-05' GROUP BY 1 ORDER BY 1;
