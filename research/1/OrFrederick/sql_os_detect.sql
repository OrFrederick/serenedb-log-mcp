WITH p AS (
  SELECT instance_id, min(ts) first_ts,
    max(CASE WHEN content LIKE '%seconds to spawn%' THEN regexp_extract(content, 'Took ([0-9.]+) seconds to spawn', 1)::double END) spawn_s
  FROM openstack WHERE instance_id IS NOT NULL GROUP BY 1),
b AS (SELECT median(spawn_s) med, median(abs(spawn_s - (SELECT median(spawn_s) FROM p))) mad FROM p)
SELECT instance_id, first_ts, spawn_s, round(((spawn_s - med) / (1.4826 * mad))::numeric, 1) robust_z
FROM p, b WHERE spawn_s > med + 5 * 1.4826 * mad ORDER BY spawn_s DESC
