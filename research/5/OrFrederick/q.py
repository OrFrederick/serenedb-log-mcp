"""Run SQL against SereneDB; print rows and wall time. Usage: q.py "SQL" [...]"""
import sys, time, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
with psycopg.connect(DSN, autocommit=True) as c:
    for sql in sys.argv[1:]:
        t = time.time(); cur = c.execute(sql)
        rows = cur.fetchall() if cur.description else []
        print(f"-- {sql.strip()[:200]}\n-- {len(rows)} rows, {time.time()-t:.2f}s")
        if cur.description: print(" | ".join(d.name for d in cur.description))
        for r in rows[:60]: print(" | ".join(str(x) for x in r))
