import time, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
SLOW = "select count(*) from bgl a join range(30) b on true where regexp_matches(a.content || b.range::text, '[a-z]+[0-9]{3}x')"
for setting in ["RESET statement_timeout", "SET statement_timeout = 1000"]:
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(setting)
        t = time.perf_counter()
        try:
            print(setting, c.execute(SLOW).fetchone())
        except Exception as e:
            print(setting, "ERR", type(e).__name__, str(e)[:200])
        print(f"  elapsed {time.perf_counter()-t:.2f}s")
