-- 06_openstack.sql: per-instance timelines, the 4 labelled anomaly instances, and API latency.
-- Anomaly UUIDs from anomaly_labels.txt.

DROP TABLE IF EXISTS r1e_os_anom;
CREATE TABLE r1e_os_anom AS SELECT * FROM (VALUES
  ('544fd51c-4edc-4780-baae-ba1d80a0acfc'), ('ae651dff-c7ad-43d6-ac96-bbcd820ccca8'),
  ('a445709b-6ad0-40ec-8860-bec60b6ca0c2'), ('1643649d-2f42-4303-bfcd-7798baec19f9')) v(instance_id);

-- per-instance lifecycle features
DROP TABLE IF EXISTS r1e_os_inst;
CREATE TABLE r1e_os_inst AS
WITH e AS (
  SELECT source_file, instance_id, ts, level, message,
         regexp_extract(message, 'Took ([0-9.]+) seconds to spawn', 1) spawn_s,
         regexp_extract(message, 'Took ([0-9.]+) seconds to build', 1) build_s,
         regexp_extract(message, 'Took ([0-9.]+) seconds to destroy', 1) destroy_s,
         regexp_extract(message, 'Took ([0-9.]+) seconds to deallocate', 1) dealloc_s
  FROM openstack WHERE instance_id IS NOT NULL)
SELECT source_file, instance_id, count(*) entries, min(ts) t_first, max(ts) t_last,
       epoch_us(max(ts)) / 1e6 - epoch_us(min(ts)) / 1e6 AS life_s,
       max(nullif(spawn_s, ''))::double spawn_s, max(nullif(build_s, ''))::double build_s,
       max(nullif(destroy_s, ''))::double destroy_s, max(nullif(dealloc_s, ''))::double dealloc_s,
       (epoch_us(min(ts) FILTER (WHERE message LIKE '%VM Started%')) - epoch_us(min(ts) FILTER (WHERE message LIKE '%Creating image%'))) / 1e6 AS image_to_start_s,
       (epoch_us(min(ts) FILTER (WHERE message LIKE '%VM Resumed%')) - epoch_us(min(ts) FILTER (WHERE message LIKE '%VM Paused%'))) / 1e6 AS paused_s,
       (epoch_us(min(ts) FILTER (WHERE message LIKE '%Creating image%')) - epoch_us(min(ts) FILTER (WHERE message LIKE '%Claim successful%'))) / 1e6 AS claim_to_image_s,
       count(*) FILTER (WHERE message LIKE '%Claim successful%') n_claim,
       count(*) FILTER (WHERE message LIKE '%spawned successfully%') n_spawned,
       count(*) FILTER (WHERE message LIKE '%Terminating instance%') n_term,
       count(*) FILTER (WHERE message LIKE '%VM Stopped%') n_stopped,
       count(*) FILTER (WHERE level <> 'INFO') n_nonINFO,
       instance_id IN (SELECT instance_id FROM r1e_os_anom) AS is_anom
FROM e GROUP BY source_file, instance_id;

-- the 4 anomalies vs everyone else (same file and all files)
SELECT source_file, is_anom, count(*) instances, median(entries) med_entries,
       round(median(spawn_s), 2) med_spawn, round(quantile_cont(spawn_s, 0.95), 2) p95_spawn, round(max(spawn_s), 2) max_spawn,
       round(median(build_s), 2) med_build, round(median(image_to_start_s), 2) med_img2start,
       round(median(paused_s), 2) med_paused, round(median(claim_to_image_s), 2) med_claim2img,
       round(median(destroy_s), 2) med_destroy, sum(n_nonINFO) nonINFO
FROM r1e_os_inst GROUP BY ALL ORDER BY source_file, is_anom;

-- each anomaly instance with its rank among the instances of its file
SELECT instance_id, source_file, is_anom, entries, spawn_s, build_s, round(image_to_start_s, 2) img2start, round(paused_s, 2) paused,
       round(claim_to_image_s, 2) claim2img, destroy_s, dealloc_s, n_nonINFO,
       rank() OVER (PARTITION BY source_file ORDER BY spawn_s DESC NULLS LAST) spawn_rank,
       count(*) OVER (PARTITION BY source_file) n_in_file
FROM r1e_os_inst QUALIFY is_anom OR rank() OVER (PARTITION BY source_file ORDER BY spawn_s DESC NULLS LAST) <= 6
ORDER BY source_file, spawn_s DESC NULLS LAST;

-- incomplete lifecycles (missing events): instances whose entry count or key events differ from the modal 25-line lifecycle
SELECT source_file, entries, n_claim, n_spawned, n_term, n_stopped, count(*) instances, count(*) FILTER (WHERE is_anom) anom
FROM r1e_os_inst GROUP BY ALL ORDER BY instances DESC LIMIT 15;

-- z-score detector: spawn time vs the abnormal file's other instances (does a simple threshold find exactly the 4?)
WITH s AS (SELECT avg(spawn_s) mu, stddev_samp(spawn_s) sd, median(spawn_s) med FROM r1e_os_inst WHERE NOT is_anom)
SELECT i.instance_id, i.source_file, i.is_anom, i.spawn_s, round((i.spawn_s - s.mu) / s.sd, 1) z_spawn
FROM r1e_os_inst i, s WHERE i.spawn_s > s.med * 1.5 ORDER BY i.spawn_s DESC LIMIT 15;

-- non-INFO lines in the abnormal file, by template and time (failure evidence vs no evidence)
SELECT level, component, left(regexp_replace(message, '[0-9a-f]{8}-[0-9a-f-]{27}|[0-9a-f]{40}|[0-9]+', '<*>', 'g'), 110) tmpl, count(*) n,
       min(ts) first_ts, max(ts) last_ts
FROM openstack WHERE source_file = 'openstack_abnormal.log' AND level <> 'INFO' GROUP BY ALL ORDER BY n DESC;

-- API latency per 5-minute bucket (nova.osapi_compute.wsgi.server 'time: X')
DROP TABLE IF EXISTS r1e_os_api;
CREATE TABLE r1e_os_api AS
SELECT source_file, ts, request_id, regexp_extract(message, '"([A-Z]+) ', 1) method,
       regexp_replace(regexp_extract(message, '"[A-Z]+ ([^ ?]+)', 1), '[0-9a-f]{8}-[0-9a-f-]{27}|[0-9a-f]{32}', '<id>', 'g') path,
       regexp_extract(message, 'status: ([0-9]+)', 1)::int status,
       regexp_extract(message, 'time: ([0-9.]+)', 1)::double latency_s
FROM openstack WHERE component = 'nova.osapi_compute.wsgi.server' AND message LIKE '%time: %';

SELECT source_file, method, path, status, count(*) n, round(quantile_cont(latency_s, 0.5), 3) p50,
       round(quantile_cont(latency_s, 0.95), 3) p95, round(quantile_cont(latency_s, 0.99), 3) p99, round(max(latency_s), 3) mx
FROM r1e_os_api GROUP BY ALL ORDER BY source_file, n DESC;

-- latency degradation: 5-minute buckets whose p95 is > 3x the file's median-bucket p95
WITH b AS (SELECT source_file, time_bucket(INTERVAL 5 MINUTE, ts) bucket, count(*) n,
                  quantile_cont(latency_s, 0.5) p50, quantile_cont(latency_s, 0.95) p95, quantile_cont(latency_s, 0.99) p99,
                  count(*) FILTER (WHERE status >= 400) errors
           FROM r1e_os_api GROUP BY 1, 2),
     m AS (SELECT source_file, median(p95) med_p95, median(p50) med_p50 FROM b GROUP BY 1)
SELECT b.source_file, b.bucket, b.n, round(b.p50, 3) p50, round(b.p95, 3) p95, round(b.p99, 3) p99, b.errors,
       round(b.p95 / m.med_p95, 1) p95_ratio
FROM b JOIN m USING (source_file)
QUALIFY b.p95 > 3 * m.med_p95 OR row_number() OVER (PARTITION BY b.source_file ORDER BY b.p95 DESC) <= 3
ORDER BY b.source_file, b.bucket;

-- per-file bucket summary (is there any degradation at all?)
WITH b AS (SELECT source_file, time_bucket(INTERVAL 5 MINUTE, ts) bucket, quantile_cont(latency_s, 0.95) p95, quantile_cont(latency_s, 0.5) p50 FROM r1e_os_api GROUP BY 1, 2)
SELECT source_file, count(*) buckets, round(min(p50), 3) min_p50, round(median(p50), 3) med_p50, round(max(p50), 3) max_p50,
       round(median(p95), 3) med_p95, round(max(p95), 3) max_p95 FROM b GROUP BY 1 ORDER BY 1;

-- API request latency in the minute windows around the 4 anomalies vs the rest of the abnormal file
WITH w AS (SELECT i.instance_id, i.t_first - INTERVAL 1 MINUTE AS w0, i.t_last + INTERVAL 1 MINUTE AS w1 FROM r1e_os_inst i WHERE is_anom)
SELECT CASE WHEN EXISTS (SELECT 1 FROM w WHERE a.ts BETWEEN w.w0 AND w.w1) THEN 'inside anomaly windows' ELSE 'rest of abnormal file' END win,
       count(*) n, round(quantile_cont(latency_s, 0.5), 3) p50, round(quantile_cont(latency_s, 0.95), 3) p95,
       round(quantile_cont(latency_s, 0.99), 3) p99, count(*) FILTER (WHERE status >= 400) errors
FROM r1e_os_api a WHERE source_file = 'openstack_abnormal.log' GROUP BY 1;

-- HTTP status distribution (request failures are explicit status codes, latency is a separate signal)
SELECT source_file, status, count(*) n FROM r1e_os_api GROUP BY ALL ORDER BY source_file, n DESC;
