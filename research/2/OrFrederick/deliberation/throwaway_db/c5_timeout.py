"""Claim 5: statement_timeout and cancel_safe."""
import time, threading, psycopg
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"
SLOW = "SELECT regexp_replace(content, '[0-9]+', '#', 'g') t, count(*) FROM bgl GROUP BY t ORDER BY 2 DESC LIMIT 3"

with psycopg.connect(DSN, autocommit=True) as c:
    t = time.time()
    c.execute(SLOW).fetchall()
    print(f"baseline slow query: {time.time()-t:.2f}s", flush=True)

with psycopg.connect(DSN, autocommit=True) as c:
    c.execute("SET statement_timeout = '500ms'")
    print("SHOW statement_timeout ->", c.execute("SHOW statement_timeout").fetchone())
    t = time.time()
    try:
        c.execute(SLOW).fetchall()
        print(f"statement_timeout: NOT enforced, finished in {time.time()-t:.2f}s")
    except Exception as e:
        print(f"statement_timeout: {type(e).__name__} after {time.time()-t:.2f}s: {str(e)[:200]}")
    try:
        print("conn usable after:", c.execute("SELECT 1").fetchone())
    except Exception as e:
        print("conn unusable:", e)

with psycopg.connect(DSN, autocommit=True) as c:
    tm = threading.Timer(0.5, c.cancel_safe)
    tm.start()
    t = time.time()
    try:
        c.execute(SLOW).fetchall()
        print(f"cancel_safe: NOT effective, finished in {time.time()-t:.2f}s")
    except Exception as e:
        print(f"cancel_safe: {type(e).__name__} after {time.time()-t:.2f}s: {str(e)[:200]}")
    try:
        print("conn usable after:", c.execute("SELECT 1").fetchone())
    except Exception as e:
        print("conn unusable:", e)

with psycopg.connect(DSN, autocommit=True) as c:
    print("new session SHOW statement_timeout ->", c.execute("SHOW statement_timeout").fetchone())
