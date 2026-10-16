import time, threading, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
SLOW = "select count(*) from bgl a join (select * from range(400)) b on true where a.content ilike '%x%y%z%q%'"
for setting in ["SET statement_timeout = 300", "SET statement_timeout = '300ms'"]:
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(setting)
        print(setting, "->", c.execute("select current_setting('statement_timeout')").fetchone())
        t = time.perf_counter()
        try:
            print(c.execute(SLOW).fetchone())
        except Exception as e:
            print("ERR", type(e).__name__, str(e)[:200])
        print(f"  elapsed {time.perf_counter()-t:.2f}s")
# client-side cancel
with psycopg.connect(DSN, autocommit=True) as c:
    timer = threading.Timer(0.3, c.cancel_safe if hasattr(c, "cancel_safe") else c.cancel)
    timer.start(); t = time.perf_counter()
    try:
        print(c.execute(SLOW).fetchone())
    except Exception as e:
        print("cancel ->", type(e).__name__, str(e)[:200])
    print(f"  elapsed {time.perf_counter()-t:.2f}s")
    print("conn still usable:", c.execute("select 1").fetchone())
