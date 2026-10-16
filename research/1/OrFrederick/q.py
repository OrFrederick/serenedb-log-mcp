"""Run SQL against SereneDB, print rows + wall time. Usage: q.py "SQL" [maxrows]"""
import sys, time, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
sql = sys.argv[1] if sys.argv[1] != "-" else sys.stdin.read()
n = int(sys.argv[2]) if len(sys.argv) > 2 else 50
with psycopg.connect(DSN, autocommit=True) as c:
    for stmt in [s for s in sql.split(";\n") if s.strip()]:
        t = time.perf_counter(); cur = c.execute(stmt)
        rows = cur.fetchall() if cur.description else []
        dt = time.perf_counter() - t
        if cur.description: print("|".join(d.name for d in cur.description))
        for r in rows[:n]: print("|".join(str(x) for x in r))
        print(f"-- {len(rows)} rows, {dt:.3f}s")
