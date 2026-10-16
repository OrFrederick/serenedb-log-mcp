#!/usr/bin/env python3
"""q.py "<sql>" — run SQL on local SereneDB, print rows + wall time."""
import sys, time, psycopg
with psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True) as c:
    for sql in sys.argv[1:]:
        t=time.time(); cur=c.execute(sql)
        rows = cur.fetchall() if cur.description else []
        if cur.description: print(" | ".join(d.name for d in cur.description))
        for r in rows[:200]: print(" | ".join(map(str,r)))
        print(f"-- {len(rows)} rows, {time.time()-t:.3f}s")
