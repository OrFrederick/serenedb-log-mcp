-- Deliberation #5 verification queries (SereneDB 26.09.1.3, dev-loader tables). Run with ../q.py. Outputs in v*_out.txt.
-- V1 (v1_incidents.sql builds r5d_mntf_inc: KERNMNTF Incidents, #1 definition, gap 300 s, merge_scope=all)
select count(*) inc, sum(lines), sum(case when mps>=64 then 1 else 0 end) mw64, sum(case when nodes=1024 then 1 else 0 end) all1024 from r5d_mntf_inc;
select case when t0 < '2005-08-31 18:41' then 'before' when t0 <= '2005-09-16 20:36' then 'window' else 'after' end ph, count(*) inc, sum(lines) lines, sum(gb1_lines) gb1, count(distinct t0::date) days, sum(case when nodes=1024 then 1 else 0 end) n1024, sum(case when nodes>=512 then 1 else 0 end) n512, sum(case when mps>=64 then 1 else 0 end) mp64, min(t0), max(t1) from r5d_mntf_inc group by 1 order by 1;
select median(nodes) from r5d_mntf_inc where gb1_lines>0;
select count(*), count(distinct t0::date), sum(case when nodes=1024 then 1 else 0 end), sum(case when nodes>=512 then 1 else 0 end) from r5d_mntf_inc where gb1_lines>0;
-- V4 machine expansion
select date_trunc('month', ts) m, count(distinct node) io_nodes, count(distinct rack) racks, count(*) lines from bgl where node like '%-I:%' group by 1 order by 1;
select first_d, count(*) racks from (select rack, min(ts)::date first_d from bgl where rack is not null and node ~ '^R' group by rack) x group by 1 order by 1;
-- V5 KERNMNTF message variants; APPSEV recent rate (lines and Incidents; v5_appsev.sql builds r5d_appsev_inc)
select regexp_replace(regexp_replace(content, '[0-9]+', 'N', 'g'), 'bglio[N]+', 'bglioN', 'g') pat, count(*), min(ts), max(ts) from bgl where alert_label='KERNMNTF' group by 1 order by 2 desc;
with d as (select max(ts) e from bgl) select sum(case when ts > e - interval '28 days' then 1 else 0 end) last28, sum(case when ts <= e - interval '28 days' then 1 else 0 end) earlier from bgl, d where alert_label='APPSEV';
select date_trunc('month', t0) m, count(*) inc, sum(lines) lines, sum(case when mps>=64 then 1 else 0 end) mw from r5d_appsev_inc group by 1 order by 1;
-- V6 Engineer window 2005-09-02
select alert_label, component, level, left(regexp_replace(content,'[0-9]+','N','g'),60) pat, count(*), min(ts), max(ts), count(distinct node) from bgl where ts between '2005-09-02 12:20' and '2005-09-02 13:10' and (alert_label<>'-' or level not in ('INFO')) group by 1,2,3,4 order by 6;
select ts, node, alert_label, left(content,90) from bgl where ts between '2005-09-02 12:30' and '2005-09-02 12:39:01.5' order by ts desc limit 8;
-- V7 FTS expression dictionary on 26.09.1.3
CREATE TEXT SEARCH DICTIONARY r5d_dict AS split_text(case := 'lower') WITH (frequency, position);
-- V8 OpenStack spawn ranks (labels from anomaly_labels.txt)
with t as (select instance_id, regexp_extract(content, 'Took ([0-9.]+) seconds to spawn', 1)::double s from openstack where content like '%seconds to spawn the instance%') select left(instance_id,8), s from t order by s desc limit 7;
with t as (select instance_id, max(case when content like '%Creating image%' then ts end) ci, min(case when content like '%VM Started%' then ts end) vs from openstack where instance_id is not null group by 1) select left(instance_id,8), epoch(vs)-epoch(ci) img from t where vs is not null order by 2 desc limit 7;
-- V9 APPSEV before /p/gb1 Incidents (10 min) vs probes 3 h later; same-node APPSEV->KERNMNTF within 60 s by variant
select count(*), sum(case when exists (select 1 from r5d_appsev_inc a where a.t0 between m.t0 - interval '10 minutes' and m.t0) then 1 else 0 end) from r5d_mntf_inc m where gb1_lines>0;
with a as (select node, epoch, alert_label, content, lag(alert_label) over (partition by node order by epoch) pl, epoch - lag(epoch) over (partition by node order by epoch) dt from bgl where alert_label<>'-' and node like '%-I:%') select case when content like '%/p/gb1%' then 'gb1' else 'block_id' end v, count(*), sum(case when pl='APPSEV' and dt<=60 then 1 else 0 end) from a where alert_label='KERNMNTF' group by 1;
