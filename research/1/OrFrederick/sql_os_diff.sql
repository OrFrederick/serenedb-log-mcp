WITH t AS (SELECT instance_id, component,
   regexp_replace(regexp_replace(content, '[0-9a-f]{8}-[0-9a-f-]{27}', '<UUID>', 'g'), '[0-9]+(\.[0-9]+)?', '<N>', 'g') tpl
  FROM openstack WHERE instance_id IN ('544fd51c-4edc-4780-baae-ba1d80a0acfc','3113fe9b-1a8f-4058-bd3d-20d6dea72bce'))
SELECT tpl, sum(CASE WHEN instance_id LIKE '544f%' THEN 1 ELSE 0 END) lab, sum(CASE WHEN instance_id LIKE '3113%' THEN 1 ELSE 0 END) unl
FROM t GROUP BY 1 HAVING sum(CASE WHEN instance_id LIKE '544f%' THEN 1 ELSE 0 END) <> sum(CASE WHEN instance_id LIKE '3113%' THEN 1 ELSE 0 END)
