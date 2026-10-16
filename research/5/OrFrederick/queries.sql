-- Golden queries behind research #5 (SereneDB 26.09.1.3, dev-loader tables bgl/openstack).
-- r5_episodes: gap-based episodes per alert category (new episode when gap > 1800 s).
create table r5_episodes as
with a as (select alert_label, ts, epoch, node, rack, midplane,
             lag(epoch) over (partition by alert_label order by epoch, line_no) as prev
           from bgl where alert_label <> '-'),
b as (select *, sum(case when prev is null or epoch - prev > 1800 then 1 else 0 end)
             over (partition by alert_label order by epoch rows between unbounded preceding and current row) as ep from a)
select alert_label, ep, min(ts) t0, max(ts) t1, count(*) n, count(distinct node) nodes,
       count(distinct midplane) mps, count(distinct rack) racks
from b group by 1, 2;
-- Q1 lines vs episodes vs spread per category (the "ranking flip")
with c as (select alert_label, count(*) lines, count(distinct node) nodes, count(distinct ts::date) days, count(distinct midplane) mps from bgl where alert_label<>'-' group by 1),
tn as (select alert_label, max(k) top_node_lines from (select alert_label, node, count(*) k from bgl where alert_label<>'-' group by 1,2) x group by 1),
e as (select alert_label, count(*) eps from r5_episodes group by 1)
select c.alert_label, lines, eps, days, nodes, mps, round(100.0*top_node_lines/lines,1) top_node_pct
from c join tn using(alert_label) join e using(alert_label) order by lines desc;
-- Q2 KERNDTLB single-node storm
select ts::date, count(*), count(distinct node) from bgl where alert_label='KERNDTLB' group by 1 order by 2 desc limit 5;
-- Q3 Lustre /p/gb1 before / window / after
select case when ts < '2005-08-31 18:41' then 'a_before' when ts <= '2005-09-16 20:36' then 'b_window' else 'c_after' end ph,
       count(*) lines, count(distinct ts::date) days, sum(case when content like '%point /p/gb1%' then 1 else 0 end) gb1
from bgl where alert_label='KERNMNTF' group by 1 order by 1;
select count(distinct node) from bgl where alert_label='KERNMNTF';            -- 1024 (all I/O nodes, node like '%-I:%')
select count(*), sum(n), sum(case when racks>=60 then 1 else 0 end) from r5_episodes where alert_label='KERNMNTF';
select t0, t1, n, nodes, racks from r5_episodes where alert_label='KERNMNTF' order by t0;
-- Q4 daily KERNMNTF vs APPSEV vs KERNTERM in the window
select ts::date d, sum(case when alert_label='KERNMNTF' then 1 else 0 end) mntf, sum(case when alert_label='APPSEV' then 1 else 0 end) appsev,
       sum(case when alert_label='KERNTERM' then 1 else 0 end) kterm from bgl where ts between '2005-08-24' and '2005-09-20' group by 1 order by 1;
-- Q5 Engineer nearby events before the first /p/gb1 failure
select ts, node, component, level, alert_label, content from bgl where ts between '2005-08-31 18:35' and '2005-08-31 18:45' order by ts;
-- Q6 OpenStack per-instance lifecycle phases
create table r5_os_phase as select source_file, instance_id,
 instance_id in ('544fd51c-4edc-4780-baae-ba1d80a0acfc','ae651dff-c7ad-43d6-ac96-bbcd820ccca8','a445709b-6ad0-40ec-8860-bec60b6ca0c2','1643649d-2f42-4303-bfcd-7798baec19f9') as labelled,
 epoch(max(case when content like '%Creating image%' then ts end)) - epoch(min(case when content like '%Attempting claim%' then ts end)) claim_to_image,
 epoch(min(case when content like '%VM Started%' then ts end)) - epoch(max(case when content like '%Creating image%' then ts end)) image_to_started,
 epoch(min(case when content like '%spawned successfully%' then ts end)) - epoch(min(case when content like '%VM Started%' then ts end)) started_to_spawned
from openstack where instance_id is not null group by 1,2,3;
select source_file, labelled, count(*), avg(claim_to_image), max(claim_to_image), avg(image_to_started), max(image_to_started), avg(started_to_spawned), max(started_to_spawned) from r5_os_phase group by 1,2;
select instance_id, image_to_started from r5_os_phase where image_to_started > 15.3 order by 2 desc;   -- 5 rows: 4 labelled + d7d68893
-- Q7 OpenStack level by file (abnormal file has 0 ERROR lines)
select source_file, level, count(*) from openstack group by 1,2 order by 1,2;
