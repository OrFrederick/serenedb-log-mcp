"""Tiny SereneDB query runner: python -I q.py "SQL" [reps]  -> prints rows + wall time."""
import sys
import time

import psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
sql = sys.argv[1]
reps = int(sys.argv[2]) if len(sys.argv) > 2 else 1
with psycopg.connect(DSN, autocommit=True) as c:
    best = None
    for _ in range(reps):
        t = time.perf_counter()
        cur = c.execute(sql)
        rows = cur.fetchall() if cur.description else None
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    if rows is not None:
        cols = [d.name for d in cur.description]
        print(" | ".join(cols))
        for r in rows[:60]:
            print(" | ".join("" if v is None else str(v)[:140] for v in r))
        print(f"({len(rows)} rows)")
    print(f"-- best of {reps}: {best:.3f}s")
