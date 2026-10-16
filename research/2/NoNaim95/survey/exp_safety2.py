"""Follow-up safety experiments: read-only transaction syntax, external file access by a
SELECT-only role, enable_external_access / lock_configuration hardening, FTS with bound params.
Only r2s_* objects; dropped at the end. Run with: .venv/bin/python -I exp_safety2.py
"""
import time

import psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"
RDSN = "host=localhost port=7890 dbname=postgres user=r2s_reader"


def run(conn, sql, params=None, label=None):
    t = time.perf_counter()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else None
        print(f"OK   {time.perf_counter()-t:7.3f}s  {label or sql[:100]!r} -> {rows if rows is None or len(rows) < 4 else rows[:3]}")
        return rows
    except Exception as e:  # noqa: BLE001
        print(f"ERR  {time.perf_counter()-t:7.3f}s  {label or sql[:100]!r} -> {type(e).__name__}: {str(e).strip()[:250]}")
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return e


admin = psycopg.connect(DSN, autocommit=True)
run(admin, "CREATE TABLE IF NOT EXISTS r2s_t(a INT)")
run(admin, "DROP ROLE IF EXISTS r2s_reader")
run(admin, "CREATE ROLE r2s_reader LOGIN")
run(admin, "GRANT SELECT ON r2s_t TO r2s_reader")

print("\n== A. read-only transaction syntaxes (as superuser)")
c = psycopg.connect(DSN)
for stmt in ["BEGIN READ ONLY", "START TRANSACTION READ ONLY", "BEGIN TRANSACTION READ ONLY"]:
    c.autocommit = True
    run(c, stmt, label=stmt)
    run(c, "INSERT INTO r2s_t VALUES (7)", label=f"INSERT after {stmt}")
    run(c, "ROLLBACK", label="ROLLBACK")
c.autocommit = True
run(c, "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY", label="SET SESSION CHARACTERISTICS ... READ ONLY")
run(c, "INSERT INTO r2s_t VALUES (8)", label="INSERT after SET SESSION CHARACTERISTICS")
run(admin, "SELECT count(*) FROM r2s_t", label="rows in r2s_t (inserts that leaked through)")

print("\n== B. external access by SELECT-only role")
rd = psycopg.connect(RDSN, autocommit=True)
run(rd, "SELECT count(*) FROM read_csv('/etc/passwd')", label="reader read_csv /etc/passwd")
run(rd, "SELECT * FROM read_text('/etc/hostname')", label="reader read_text /etc/hostname")
run(rd, "COPY (SELECT 1) TO '/tmp/r2s_reader_copy.csv'", label="reader COPY TO /tmp file")
run(rd, "SELECT * FROM glob('/var/lib/serenedb/*') LIMIT 3", label="reader glob data dir")
run(rd, "SET enable_external_access = false", label="reader SET enable_external_access=false")
run(rd, "SELECT count(*) FROM read_csv('/etc/passwd')", label="reader read_csv after disabling (session)")
run(rd, "SET enable_external_access = true", label="reader re-enable external access")
run(rd, "SET memory_limit = '100GB'", label="reader SET memory_limit (global)")
run(rd, "SET threads = 1", label="reader SET threads")
rd.close()

print("\n== C. per-connection hardening knobs (superuser on own session, then check other conn)")
h = psycopg.connect(DSN, autocommit=True)
run(h, "SET operator_memory_limit = '512MB'", label="SET operator_memory_limit")
run(h, "SELECT current_setting('operator_memory_limit')")
other = psycopg.connect(DSN, autocommit=True)
run(other, "SELECT current_setting('operator_memory_limit')", label="operator_memory_limit seen by other conn")
run(other, "SELECT current_setting('enable_external_access')", label="enable_external_access seen by other conn")

print("\n== D. FTS with bound parameters against an existing inverted index (read only)")
p = psycopg.connect(DSN, autocommit=True)
run(p, "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ %s", ("error",), "BGL @@ bound text param")
run(p, "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ %s::TSQUERY", ("error",), "BGL @@ bound ::TSQUERY")
run(p, "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ %s AND component = %s", ("error", "KERNEL"), "@@ + verbatim filter")
run(p, "SELECT line_no, ts_highlight(message) FROM r2p_bgl_idx WHERE message @@ %s ORDER BY BM25(r2p_bgl_idx.tableoid) DESC LIMIT 2",
    ("machine check",), "BM25 + ts_highlight with bound param")
run(p, "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ %s", ("error' OR 1=1 --",), "injection-looking text as @@ param")

print("\n== cleanup")
run(admin, "DROP TABLE IF EXISTS r2s_t")
run(admin, "DROP ROLE IF EXISTS r2s_reader")
