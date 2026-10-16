import sys, time, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
with psycopg.connect(DSN, autocommit=True) as c:
    for stmt in [s for s in sql.split(';\n') if s.strip()]:
        t = time.time()
        try:
            cur = c.execute(stmt)
            dt = time.time() - t
            if cur.description:
                cols = [d.name for d in cur.description]
                rows = cur.fetchall()
                print(' | '.join(cols))
                for r in rows: print(' | '.join(str(x) for x in r))
            print(f'-- {dt*1000:.0f} ms')
        except Exception as e:
            print('ERROR', type(e).__name__, str(e).strip())
