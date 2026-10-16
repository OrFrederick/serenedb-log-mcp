--x5 Q1 group by template_id (narrow fact, 4.7M)
select template_id, count(*) n from r6d_bgl_ft group by 1 order by n desc limit 5;;
--x5 Q2 group by template_id (full-width table)
select template_id, count(*) n from r6d_bgl_full group by 1 order by n desc limit 5;;
--x5 Q3 on the fly: join raw bgl to dict at query time
select d.template_key, count(*) n from bgl b join r6d_bgl_dict d on coalesce(b.message,'')=d.message group by 1 order by n desc limit 5;;
--x3 Q4 on the fly: mask per row at query time (no stored artefact)
select r6d_mask_v10(message) k, count(*) n from bgl group by 1 order by n desc limit 5;;
--x5 Q5 daily trend for top-5 templates
select time_bucket(interval '1 day', ts) d, template_id, count(*) n from r6d_bgl_ft where template_id in (select template_id from r6d_bgl_tpl order by n desc limit 5) group by 1,2 order by 1,2 limit 5;;
--x5 Q6 daily trend for all templates (12.5k cells)
select count(*) from (select time_bucket(interval '1 day', ts) d, template_id, count(*) n from r6d_bgl_ft group by 1,2);;
--x5 Q7 top 3 templates per component (window)
select component, template_id, n from (select component, template_id, count(*) n, row_number() over (partition by component order by count(*) desc) rk from r6d_bgl_ft group by 1,2) where rk<=3 order by component, rk limit 5;;
--x5 Q8 alert-tagged rows by template with node spread
select t.template_key, count(*) n, count(distinct f.node) nodes from r6d_bgl_ft f join r6d_bgl_tpl t using(template_id) where f.alert_tag<>'-' group by 1 order by n desc limit 5;;
--x5 Q9 one template's daily trend (point filter)
select time_bucket(interval '1 day', ts) d, count(*) from r6d_bgl_ft where template_id='6265c7394fe834d8' group by 1 order by 1 limit 3;;
