"""Safety experiments for issue #2 (survey track): read-only role, read-only transactions,
statement timeout, client-side cancel, server-side bind parameters (incl. TSQUERY @@ $1).

Run: /Users/deni/hackathon/tools/.venv/bin/python exp_safety.py
Creates only r2s_* objects and drops them at the end.
"""
import threading
import time

import psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"


def run(conn, sql, params=None, label=None):
    t = time.perf_counter()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else None
        dt = time.perf_counter() - t
        print(f"OK   {dt:7.3f}s  {label or sql[:90]!r} -> {rows if rows is None or len(rows) < 6 else rows[:5]}")
        return rows
    except Exception as e:  # noqa: BLE001
        dt = time.perf_counter() - t
        print(f"ERR  {dt:7.3f}s  {label or sql[:90]!r} -> {type(e).__name__}: {str(e).strip()[:300]}")
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return e


def main():
    admin = psycopg.connect(DSN, autocommit=True)
    run(admin, "SELECT version()")

    print("\n== 1. statement_timeout (docs say: accepted but not enforced)")
    run(admin, "SET statement_timeout = 500")
    run(admin, "SHOW statement_timeout")
    slow = ("SELECT count(*) FROM bgl a, range(8) r "
            "WHERE a.message LIKE '%' || r.range::varchar || 'zz%'")
    run(admin, slow, label="slow query under statement_timeout=500ms")
    run(admin, "RESET statement_timeout")

    print("\n== 2. client-side cancel via psycopg Connection.cancel_safe() after 1.0s")
    c2 = psycopg.connect(DSN, autocommit=True)
    timer = threading.Timer(1.0, lambda: c2.cancel_safe(timeout=5))
    timer.start()
    run(c2, slow, label="slow query, cancel_safe() at 1.0s")
    timer.cancel()
    run(c2, "SELECT 42", label="connection still usable after cancel")

    print("\n== 3. server-side cancel via pg_cancel_backend(pid) from another connection")
    c3 = psycopg.connect(DSN, autocommit=True)
    pid = c3.info.backend_pid
    print("backend pid:", pid)
    timer = threading.Timer(1.0, lambda: run(admin, "SELECT pg_cancel_backend(%s)", (pid,), "pg_cancel_backend"))
    timer.start()
    run(c3, slow, label="slow query, pg_cancel_backend at 1.0s")
    timer.join()

    print("\n== 4. read-only transaction / session")
    run(admin, "CREATE TABLE IF NOT EXISTS r2s_t(a INT)")
    ro = psycopg.connect(DSN, autocommit=True)
    run(ro, "SET default_transaction_read_only = on")
    run(ro, "INSERT INTO r2s_t VALUES (1)", label="INSERT with default_transaction_read_only=on")
    run(ro, "SELECT count(*) FROM r2s_t", label="SELECT with default_transaction_read_only=on")
    ro2 = psycopg.connect(DSN)  # non-autocommit
    run(ro2, "SET TRANSACTION READ ONLY")
    run(ro2, "INSERT INTO r2s_t VALUES (2)", label="INSERT inside SET TRANSACTION READ ONLY")
    ro2.rollback()
    run(ro, "SET access_mode = 'READ_ONLY'", label="SET access_mode=READ_ONLY (session?)")

    print("\n== 5. read-only LOGIN role with SELECT grant")
    run(admin, "DROP ROLE IF EXISTS r2s_reader")
    run(admin, "CREATE ROLE r2s_reader LOGIN")
    run(admin, "GRANT SELECT ON r2s_t TO r2s_reader")
    run(admin, "GRANT SELECT ON bgl TO r2s_reader")
    try:
        rd = psycopg.connect("host=localhost port=7890 dbname=postgres user=r2s_reader", autocommit=True)
        run(rd, "SELECT current_user")
        run(rd, "SELECT count(*) FROM r2s_t", label="reader SELECT granted table")
        run(rd, "INSERT INTO r2s_t VALUES (3)", label="reader INSERT")
        run(rd, "CREATE TABLE r2s_evil(a INT)", label="reader CREATE TABLE")
        run(rd, "DROP TABLE r2s_t", label="reader DROP TABLE")
        run(rd, "SELECT count(*) FROM openstack", label="reader SELECT ungranted table")
        run(rd, "SELECT count(*) FROM read_csv('/etc/passwd')", label="reader read_csv local file")
        run(rd, "SELECT count(*) FROM read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') LIMIT 1",
            label="reader read_parquet remote (skipped if slow)") if False else None
        rd.close()
    except Exception as e:  # noqa: BLE001
        print("reader connect failed:", e)

    print("\n== 6. server-side bind parameters")
    p = psycopg.connect(DSN, autocommit=True)
    run(p, "SELECT count(*) FROM bgl WHERE component = %s AND ts >= %s AND ts < %s",
        ("KERNEL", "2005-06-03", "2005-06-04"), "client-side-style %s params (psycopg sends $1.. extended protocol)")
    run(p, "SELECT count(*) FROM bgl WHERE component = %s", ("KERNEL",), "simple param")
    run(p, "SELECT %s::INT + 1", (41,), "typed param")
    # injection attempt as a value
    run(p, "SELECT count(*) FROM bgl WHERE component = %s", ("KERNEL' OR '1'='1",), "injection string as value")
    # prepared statement explicitly
    with p.cursor() as cur:
        t = time.perf_counter()
        for _ in range(3):
            cur.execute("SELECT count(*) FROM bgl WHERE node = %s", ("R02-M1-N0-C:J12-U11",), prepare=True)
            cur.fetchall()
        print(f"OK   prepared x3 {time.perf_counter()-t:.3f}s")
    # binary protocol
    with p.cursor(binary=True) as cur:
        try:
            cur.execute("SELECT ts, node FROM bgl WHERE component = %s LIMIT 2", ("KERNEL",))
            print("OK   binary cursor ->", cur.fetchall())
        except Exception as e:  # noqa: BLE001
            print("ERR  binary cursor ->", e)
            p.rollback()

    print("\n== 7. full-text search with bound TSQUERY / text parameters")
    run(p, "SELECT indexname, tablename FROM pg_indexes WHERE tablename IN ('bgl','openstack')", label="list indexes")

    print("\n== cleanup")
    run(admin, "DROP TABLE IF EXISTS r2s_t")
    run(admin, "DROP TABLE IF EXISTS r2s_evil")
    run(admin, "REVOKE SELECT ON bgl FROM r2s_reader")
    run(admin, "DROP ROLE IF EXISTS r2s_reader")


if __name__ == "__main__":
    main()
