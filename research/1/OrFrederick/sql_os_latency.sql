WITH m AS (SELECT time_bucket(interval '1 minute', ts) b, count(*) reqs,
    quantile_cont(latency_s, 0.95) p95, max(latency_s) mx
  FROM openstack WHERE latency_s IS NOT NULL GROUP BY 1),
s AS (SELECT median(p95) med, median(abs(p95 - (SELECT median(p95) FROM m))) mad FROM m)
SELECT b, reqs, round(p95::numeric,3) p95, round(mx::numeric,3) mx, round(((p95-med)/(1.4826*mad))::numeric,1) z
FROM m, s ORDER BY z DESC LIMIT 8
