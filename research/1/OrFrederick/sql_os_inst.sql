WITH l AS (SELECT * FROM (VALUES ('544fd51c-4edc-4780-baae-ba1d80a0acfc'),('ae651dff-c7ad-43d6-ac96-bbcd820ccca8'),('a445709b-6ad0-40ec-8860-bec60b6ca0c2'),('1643649d-2f42-4303-bfcd-7798baec19f9')) v(instance_id))
SELECT o.instance_id, (o.instance_id IN (SELECT instance_id FROM l)) labelled, count(*) lines,
  min(ts) first_ts, round(epoch(max(ts)) - epoch(min(ts))) life_s,
  sum(CASE WHEN level <> 'INFO' THEN 1 ELSE 0 END) non_info,
  sum(CASE WHEN content LIKE '%Took % seconds to spawn%' THEN 1 ELSE 0 END) spawn_msgs,
  sum(CASE WHEN content LIKE '%Terminating instance%' THEN 1 ELSE 0 END) term_msgs,
  sum(CASE WHEN content LIKE '%Deleting instance files%' THEN 1 ELSE 0 END) del_msgs
FROM openstack o WHERE source_file LIKE '%abnormal%' AND instance_id IS NOT NULL
GROUP BY 1,2 ORDER BY labelled DESC, lines DESC LIMIT 12
