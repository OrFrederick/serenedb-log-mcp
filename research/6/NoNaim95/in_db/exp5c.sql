--x3 N1 templates first seen in the last 7 days of the corpus (relative to max ts), with counts and node spread
select template_key, first_seen, n, nodes from r6d_bgl_tpl
 where first_seen >= (select max(ts) from bgl) - interval '7 days' order by first_seen limit 10;;
--x3 N2 new templates per week (how many patterns appear for the first time each week)
select time_bucket(interval '7 days', first_seen) wk, count(*) new_templates, sum(n) lines from r6d_bgl_tpl group by 1 order by 1 limit 40;;
--x3 N3 "unseen vs baseline" fully on the fly: templates in a window absent from a baseline window
with w as (select distinct template_id from r6d_bgl_ft where ts >= timestamp '2005-12-01'),
     b as (select distinct template_id from r6d_bgl_ft where ts <  timestamp '2005-12-01')
select t.template_key, t.n, t.first_seen from w join r6d_bgl_tpl t using(template_id) where template_id not in (select template_id from b) order by t.first_seen limit 10;;
--x1 C re-time: CTAS full-width + per-row mask
create or replace table r6d_bgl_full as select *, substr(md5(r6d_mask_v10(coalesce(message,''))),1,16) template_id from bgl;;
--x1 C2 re-time: CTAS full-width + dict join
create or replace table r6d_bgl_full_b as select b.*, substr(md5(d.template_key),1,16) template_id from bgl b join r6d_bgl_dict d on coalesce(b.message,'') = d.message;;
--x1 OpenStack: dictionary + fact table
create or replace table r6d_os_dict as select message, r6d_mask_v10(message) template_key from (select distinct coalesce(message,'') message from openstack);;
create or replace table r6d_os_ft as select o.line_no, o.ts, o.component, o.level, o.instance_id, substr(md5(d.template_key),1,16) template_id, d.template_key from openstack o join r6d_os_dict d on coalesce(o.message,'')=d.message;;
--x3 OS top templates by component on ERROR/WARNING
select component, template_key, count(*) n from r6d_os_ft where level in ('ERROR','WARNING') group by 1,2 order by n desc limit 8;;
--x3 OS templates touching the 4 anomaly instances vs others
select template_key, count(*) filter (where instance_id in ('544fd51c-4edc-4780-baae-ba1d80a0acfc','ae651dff-c7ad-43d6-ac96-bbcd820ccca8','a445709b-6ad0-40ec-8860-bec60b6ca0c2','1643649d-2f42-4303-bfcd-7798baec19f9')) anom, count(*) total from r6d_os_ft where instance_id is not null group by 1 order by anom desc limit 8;;
