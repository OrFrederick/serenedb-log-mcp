-- V1: KERNMNTF Incidents under #1 definition (per Pattern, merge_scope=all, gap 300 s, epoch = UTC seconds)
create table r5d_mntf_inc as
with a as (select ts, epoch, node, rack, midplane, content like '%point /p/gb1%' as gb1 from bgl where alert_label='KERNMNTF'),
g as (select *, case when epoch - lag(epoch) over (order by epoch, node) <= 300 then 0 else 1 end is_new from a),
s as (select *, (sum(is_new) over (order by epoch, node rows unbounded preceding))::bigint inc from g)
select inc, min(ts) t0, max(ts) t1, count(*) lines, sum(case when gb1 then 1 else 0 end) gb1_lines,
       count(distinct node) nodes, count(distinct midplane) mps, count(distinct rack) racks
from s group by inc;
