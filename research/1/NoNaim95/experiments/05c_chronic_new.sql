-- 05c_chronic_new.sql: (d) chronic background vs new pattern, and pattern-level slow trickle.
-- Pattern = masked-message key. Corpus spans 2005-06-03 .. 2006-01-04 (ts = log wall clock).

DROP TABLE IF EXISTS r1e_pat;
CREATE TABLE r1e_pat AS
SELECT pkey k, count(*) n, min(ts) first_seen, max(ts) last_seen, count(DISTINCT date_trunc('day', ts)) active_days,
       count(DISTINCT node) nodes, max(alert_tag) alert_tag, max(level) lvl
FROM r1e_bgl GROUP BY pkey;

-- chronic vs new vs gone: classes by first/last-seen and activity
WITH c AS (SELECT max(last_seen) corpus_end, min(first_seen) corpus_start FROM r1e_pat)
SELECT CASE WHEN first_seen >= corpus_end - INTERVAL 14 DAY THEN 'new (first seen in last 14 d)'
            WHEN active_days >= 60 THEN 'chronic (>=60 active days)'
            WHEN last_seen < corpus_end - INTERVAL 30 DAY THEN 'gone (not seen in last 30 d)'
            ELSE 'intermittent' END cls,
       count(*) patterns, sum(n) entries, count(*) FILTER (WHERE alert_tag <> '-') alert_patterns
FROM r1e_pat, c GROUP BY 1 ORDER BY 3 DESC;

-- the new patterns (first seen in the last 14 days), biggest first
WITH c AS (SELECT max(last_seen) corpus_end FROM r1e_pat)
SELECT left(k, 90) k, n, nodes, active_days, first_seen, alert_tag, lvl
FROM r1e_pat, c WHERE first_seen >= corpus_end - INTERVAL 14 DAY ORDER BY n DESC LIMIT 15;

-- chronic background: most active-days patterns
SELECT left(k, 90) k, n, nodes, active_days, first_seen, last_seen, alert_tag, lvl FROM r1e_pat ORDER BY active_days DESC, n DESC LIMIT 12;

-- weekly rate jump: Incidents per week per pattern (mask key, G=60, G'=300) vs trailing 8-week baseline
DROP TABLE IF EXISTS r1e_wk;
CREATE TABLE r1e_wk AS
WITH w AS (SELECT DISTINCT date_trunc('week', make_timestamp_ms((t0 * 1000)::bigint)) wk FROM r1e_inc WHERE kt = 'mask' AND g = 60 AND gp = 300),
     ks AS (SELECT DISTINCT k FROM r1e_inc WHERE kt = 'mask' AND g = 60 AND gp = 300),
     obs AS (SELECT k, date_trunc('week', make_timestamp_ms((t0 * 1000)::bigint)) wk, count(*) inc, sum(n) entries
             FROM r1e_inc WHERE kt = 'mask' AND g = 60 AND gp = 300 GROUP BY 1, 2),
     grid AS (SELECT ks.k, w.wk, coalesce(obs.inc, 0) inc, coalesce(obs.entries, 0) entries
              FROM ks CROSS JOIN w LEFT JOIN obs ON obs.k = ks.k AND obs.wk = w.wk)
SELECT *, avg(inc) OVER tr base_mean, stddev_samp(inc) OVER tr base_sd, count(*) OVER tr base_weeks
FROM grid WINDOW tr AS (PARTITION BY k ORDER BY wk ROWS BETWEEN 8 PRECEDING AND 1 PRECEDING);

-- biggest week-over-baseline jumps (z with sd floored at 1 to avoid divide-by-zero on silent patterns)
SELECT left(k, 80) k, wk, inc, round(base_mean, 2) base_mean, round(base_sd, 2) base_sd,
       round((inc - base_mean) / greatest(coalesce(base_sd, 0), 1), 1) z, entries
FROM r1e_wk WHERE base_weeks = 8 ORDER BY (inc - base_mean) / greatest(coalesce(base_sd, 0), 1) DESC LIMIT 15;

-- last full week of the corpus: which patterns jump (what a Team Lead would see "this week")
SELECT left(k, 80) k, wk, inc, round(base_mean, 2) base_mean, round((inc - base_mean) / greatest(coalesce(base_sd, 0), 1), 1) z, entries
FROM r1e_wk WHERE base_weeks = 8 AND wk = (SELECT max(wk) FROM r1e_wk) - INTERVAL 7 DAY AND inc > 0
ORDER BY z DESC LIMIT 10;

-- pattern-level slow trickle: many small Incidents spread over many days (chronic low-rate)
SELECT left(i.k, 80) k, count(*) incidents, sum(i.n) entries, median(i.n) med_entries_per_inc,
       p.active_days, round(sum(i.n)::double / p.active_days, 1) entries_per_active_day, p.nodes
FROM r1e_inc i JOIN r1e_pat p ON p.k = i.k
WHERE i.kt = 'mask' AND i.g = 60 AND i.gp = 300
GROUP BY i.k, p.active_days, p.nodes HAVING p.active_days >= 60 AND median(i.n) <= 3
ORDER BY incidents DESC LIMIT 12;
