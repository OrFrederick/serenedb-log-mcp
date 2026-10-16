-- A: dictionary of distinct messages -> template key (mask runs once per distinct message). NULL message -> ''
create or replace table r6d_bgl_dict as
  select message, r6d_mask_v10(message) template_key from (select distinct coalesce(message,'') message from bgl);;
select count(*) msgs, count(distinct template_key) keys from r6d_bgl_dict;;
-- B: CTAS narrow fact table with template_id via dict join
create or replace table r6d_bgl_ft as
  select b.line_no, b.ts, b.node, b.component, b.level, b.alert_tag, substr(md5(d.template_key),1,16) template_id
  from bgl b join r6d_bgl_dict d on coalesce(b.message,'') = d.message;;
select count(*), count(distinct template_id) from r6d_bgl_ft;;
-- templates table: stable id = first 16 hex of md5(key); n, first_seen, last_seen, #nodes
create or replace table r6d_bgl_tpl as
  select f.template_id, any_value(d.template_key) template_key, count(*) n, min(f.ts) first_seen, max(f.ts) last_seen, count(distinct f.node) nodes
  from r6d_bgl_ft f join (select distinct substr(md5(template_key),1,16) template_id, template_key from r6d_bgl_dict) d using(template_id) group by 1;;
select count(*), sum(n) from r6d_bgl_tpl;;
-- C: CTAS full-width copy + template_id computed per row directly (no dict)
create or replace table r6d_bgl_full as select *, substr(md5(r6d_mask_v10(coalesce(message,''))),1,16) template_id from bgl;;
-- C2: CTAS full-width copy + template_id via dict join
create or replace table r6d_bgl_full2 as select b.*, substr(md5(d.template_key),1,16) template_id from bgl b join r6d_bgl_dict d on coalesce(b.message,'') = d.message;;
-- D: ALTER + UPDATE on a copy
create or replace table r6d_bgl_upd as select * from bgl;;
alter table r6d_bgl_upd add column template_id varchar;;
update r6d_bgl_upd set template_id = substr(md5(d.template_key),1,16) from r6d_bgl_dict d where coalesce(r6d_bgl_upd.message,'') = d.message;;
select count(*), count(template_id) from r6d_bgl_upd;;
-- E: generated column using the macro (does it work?)
create or replace table r6d_gen (message varchar, template_key varchar generated always as (r6d_mask_v10(message)) stored);;
insert into r6d_gen(message) select message from bgl limit 1000;;
select template_key, count(*) from r6d_gen group by 1 order by 2 desc limit 3;;
drop table r6d_upd_dummy_if_any_noop;;
