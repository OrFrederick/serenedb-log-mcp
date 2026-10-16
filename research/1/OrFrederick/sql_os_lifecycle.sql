WITH e AS (
  SELECT source_file, instance_id, epoch(ts) t, content FROM openstack WHERE instance_id IS NOT NULL),
p AS (
  SELECT source_file, instance_id, count(*) lines,
    max(CASE WHEN content LIKE '%seconds to spawn%' THEN regexp_extract(content, 'Took ([0-9.]+) seconds to spawn', 1)::double END) spawn_s,
    max(CASE WHEN content LIKE '%seconds to build%' THEN regexp_extract(content, 'Took ([0-9.]+) seconds to build', 1)::double END) build_s,
    max(CASE WHEN content LIKE '%VM Resumed%' THEN t END) - min(CASE WHEN content LIKE '%VM Paused%' THEN t END) pause_s,
    min(CASE WHEN content LIKE '%VM Started%' THEN t END) - min(CASE WHEN content LIKE '%Creating image%' THEN t END) image_s,
    max(CASE WHEN content LIKE '%VM Stopped%' THEN t END) - min(CASE WHEN content LIKE '%Terminating instance%' THEN t END) stop_s
  FROM e GROUP BY 1,2)
SELECT source_file, instance_id IN ('544fd51c-4edc-4780-baae-ba1d80a0acfc','ae651dff-c7ad-43d6-ac96-bbcd820ccca8','a445709b-6ad0-40ec-8860-bec60b6ca0c2','1643649d-2f42-4303-bfcd-7798baec19f9') labelled,
  count(*) n, round(avg(spawn_s)::numeric,2) avg_spawn, round(max(spawn_s)::numeric,2) max_spawn,
  round(avg(pause_s)::numeric,2) avg_pause, round(max(pause_s)::numeric,2) max_pause,
  round(avg(image_s)::numeric,2) avg_image, round(avg(stop_s)::numeric,2) avg_stop, round(max(stop_s)::numeric,2) max_stop
FROM p GROUP BY 1,2 ORDER BY 1,2
