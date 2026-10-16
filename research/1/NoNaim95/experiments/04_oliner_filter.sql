-- 04_oliner_filter.sql: Oliner & Stearley (DSN 2007) LogFilter on BGL, alert_tag as category.
-- Algorithm 2.1.1 (Oliner PhD thesis, Stanford 2008, ch. 2.1.3, which reproduces the DSN'07 paper):
-- an alert is removed if ANY source reported the same category within the last T seconds;
-- the per-category "last seen" time is updated also for removed alerts (X[c] <- t_i in both branches),
-- so an alert is kept iff (t_i - t_prev_same_category) >= T  (strict "< T" removes).
-- The text says T = 5 s. Published BG/L numbers (thesis Table 2.1.2 = DSN'07 Table 4):
--   total raw 348,460 filtered 1,202; per-category values below.

-- published values (raw, filtered) for the 10 listed categories + "31 Others"
DROP TABLE IF EXISTS r1e_os07;
CREATE TABLE r1e_os07 AS SELECT * FROM (VALUES
 ('KERNDTLB', 152734, 37), ('KERNSTOR', 63491, 8), ('APPSEV', 49651, 138), ('KERNMNTF', 31531, 105),
 ('KERNTERM', 23338, 99), ('KERNREC', 6145, 9), ('APPREAD', 5983, 11), ('KERNRTSP', 3983, 260),
 ('APPRES', 2370, 13), ('APPUNAV', 2048, 3), ('OTHERS', 7186, 519)) v(cat, raw_pub, filt_pub);

-- our raw + filtered counts at T = 5 s (as stated) and T = 300 s, vs published
WITH a AS (SELECT alert_tag c, t, t - lag(t) OVER (PARTITION BY alert_tag ORDER BY t, line_no) gap
           FROM r1e_bgl WHERE alert_tag <> '-'),
     per AS (SELECT CASE WHEN c IN (SELECT cat FROM r1e_os07) THEN c ELSE 'OTHERS' END cat,
                    count(*) raw, count(*) FILTER (WHERE gap IS NULL OR gap >= 5) f5,
                    count(*) FILTER (WHERE gap IS NULL OR gap >= 300) f300, count(DISTINCT c) ncat
             FROM a GROUP BY 1)
SELECT p.cat, p.ncat, p.raw, o.raw_pub, p.f5, p.f300, o.filt_pub, p.f300 - o.filt_pub AS diff300
FROM per p JOIN r1e_os07 o ON o.cat = p.cat
UNION ALL
SELECT 'TOTAL', sum(ncat), sum(raw), 348460, sum(f5), sum(f300), 1202, sum(f300) - 1202 FROM per
ORDER BY raw DESC;

-- T sweep: total filtered count as function of T (plateau at 1202 for T in [299, 310])
WITH a AS (SELECT t - lag(t) OVER (PARTITION BY alert_tag ORDER BY t, line_no) gap FROM r1e_bgl WHERE alert_tag <> '-')
SELECT thr, count(*) FILTER (WHERE gap IS NULL OR gap >= thr) filtered,
       count(*) FILTER (WHERE gap IS NULL OR gap >= thr) - 1202 AS minus_published
FROM a, (SELECT unnest([1, 5, 10, 30, 60, 120, 180, 240, 270, 290, 295, 298, 299, 300, 301, 305, 310, 311, 315, 330, 360, 600, 1800, 3600, 86400]) AS thr) x
GROUP BY thr ORDER BY thr;

-- same with the integer epoch column (second granularity) instead of microsecond ts
WITH a AS (SELECT epoch - lag(epoch) OVER (PARTITION BY alert_tag ORDER BY epoch, line_no) gap FROM bgl WHERE alert_tag <> '-')
SELECT count(*) FILTER (WHERE gap IS NULL OR gap >= 5) f5_epoch, count(*) FILTER (WHERE gap IS NULL OR gap >= 300) f300_epoch FROM a;

-- per-category detail at T=300 for "others" (31 categories) for the record
WITH a AS (SELECT alert_tag c, t - lag(t) OVER (PARTITION BY alert_tag ORDER BY t, line_no) gap FROM r1e_bgl WHERE alert_tag <> '-')
SELECT c, count(*) raw, count(*) FILTER (WHERE gap IS NULL OR gap >= 5) f5, count(*) FILTER (WHERE gap IS NULL OR gap >= 300) f300
FROM a WHERE c NOT IN (SELECT cat FROM r1e_os07) GROUP BY c ORDER BY raw DESC;

-- the APPSEV gap (136 vs 138): APPSEV gaps closest to 300 s
WITH a AS (SELECT alert_tag c, t, t - lag(t) OVER (PARTITION BY alert_tag ORDER BY t, line_no) gap FROM r1e_bgl WHERE alert_tag <> '-')
SELECT c, make_timestamp_ms((t * 1000)::bigint) ts, round(gap, 3) gap FROM a WHERE c = 'APPSEV' AND gap BETWEEN 150 AND 600 ORDER BY abs(gap - 300) LIMIT 8;
