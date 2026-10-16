"""Run SQL statements (separated by ';;' lines) against SereneDB, print rows + timing."""
import sys, time, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
src = sys.stdin.read()
with psycopg.connect(DSN, autocommit=True) as c:
    for stmt in [s.strip() for s in src.split("\n;;\n") if s.strip()]:
        print("=== " + stmt.replace("\n", " ")[:300])
        t = time.perf_counter()
        try:
            cur = c.execute(stmt)
            rows = cur.fetchall() if cur.description else []
            dt = (time.perf_counter() - t) * 1000
            if cur.description:
                print(" | ".join(d.name for d in cur.description))
            for r in rows[:15]:
                print(" | ".join(str(x)[:160] for x in r))
            print(f"-- {len(rows)} rows, {dt:.1f} ms")
        except Exception as e:
            print(f"!! {type(e).__name__}: {str(e)[:500]}  ({(time.perf_counter()-t)*1000:.1f} ms)")
