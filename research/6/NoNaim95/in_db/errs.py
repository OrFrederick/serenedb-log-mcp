#!/usr/bin/env python3
"""errs.py <macro> <lh2 table> : show biggest GA errors (merged keys, split events)."""
import sys, psycopg
mac, tbl = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 15
with psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True) as c:
    base = f"""with d as (select content c, event_id, count(*) n from {tbl} group by 1,2),
    k as (select {mac}(c) key, event_id, sum(n) n, any_value(c) ex from d group by 1,2)"""
    print("== keys covering >1 event (merges): key | #events | lines | events")
    for r in c.execute(base + f""" select key, count(*), sum(n), string_agg(event_id||':'||n, ' ' order by n desc) from k group by key having count(*)>1 order by 3 desc limit {N}"""):
        print(" ", r[0][:140], "|", r[1], "|", r[2], "|", r[3][:200])
    print("== events split over >1 key (splits): event | template | #keys | lines | top keys")
    for r in c.execute(base + f""" , t as (select event_id, any_value(event_template) tp from {tbl} group by 1)
      select k.event_id, any_value(tp), count(*), sum(n), string_agg(key||' ['||n||']', ' || ' order by n desc) from k join t using(event_id) group by k.event_id having count(*)>1 order by 4 desc limit {N}"""):
        print(" ", r[0], "|", r[1][:100], "|", r[2], "|", r[3], "|", r[4][:400])
