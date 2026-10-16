create table r5d_appsev_inc as
with a as (select ts, epoch, node, midplane from bgl where alert_label='APPSEV'),
g as (select *, case when epoch - lag(epoch) over (order by epoch, node) <= 300 then 0 else 1 end is_new from a),
s as (select *, (sum(is_new) over (order by epoch, node rows unbounded preceding))::bigint inc from g)
select inc, min(ts) t0, count(*) lines, count(distinct node) nodes, count(distinct midplane) mps from s group by inc;
