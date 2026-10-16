"""Test statement_timeout, cancellation, read-only role on SereneDB."""
import psycopg, time, threading
DSN="host=localhost port=7890 dbname=postgres user=postgres"
SLOW = "select count(distinct regexp_replace(raw, '[0-9]', '', 'g')) from bgl"
def run(c, sql, p=None):
    t=time.time()
    try:
        r=c.execute(sql,p).fetchall() if c.execute else None
        return f"OK {time.time()-t:.2f}s {r[:3] if r else r}"
    except Exception as e:
        return f"ERR {time.time()-t:.2f}s {type(e).__name__}: {str(e).splitlines()[0][:160]}"
c=psycopg.connect(DSN, autocommit=True)
for s in ["show statement_timeout", "SET statement_timeout = '500ms'", "show statement_timeout", "select current_setting('statement_timeout')"]:
    print(s, "->", run(c, s))
print("slow under timeout 500ms ->", run(c, SLOW))
c.execute("RESET statement_timeout") if True else None
print("slow, no timeout ->", run(c, SLOW))
# libpq-style options
try:
    c2=psycopg.connect(DSN+" options='-c statement_timeout=700'", autocommit=True)
    print("conn options statement_timeout ->", run(c2,"show statement_timeout"), run(c2, SLOW))
except Exception as e: print("conn options fail", e)
# client-side cancel
c3=psycopg.connect(DSN, autocommit=True)
threading.Timer(0.5, lambda: c3.cancel_safe() if hasattr(c3,'cancel_safe') else c3.cancel()).start()
print("client cancel after 0.5s ->", run(c3, SLOW))
# pg_cancel_backend
c4=psycopg.connect(DSN, autocommit=True)
pid=c4.execute("select pg_backend_pid()").fetchone()[0]
print("backend pid", pid)
threading.Timer(0.5, lambda: print("  pg_cancel_backend ->", run(psycopg.connect(DSN,autocommit=True), "select pg_cancel_backend(%s)", [pid]))).start()
print("pg_cancel_backend after 0.5s ->", run(c4, SLOW))
# read-only
for s in ["show access_mode", "show default_transaction_read_only", "show transaction_read_only"]:
    print(s, "->", run(c, s))
# read only role
for s in ["DROP ROLE IF EXISTS r2p_reader", "CREATE ROLE r2p_reader LOGIN PASSWORD 'r2p'", "GRANT SELECT ON bgl TO r2p_reader", "GRANT SELECT ON openstack TO r2p_reader"]:
    print(s, "->", run(c, s))
try:
    rc=psycopg.connect("host=localhost port=7890 dbname=postgres user=r2p_reader password=r2p", autocommit=True)
    for s in ["select current_user", "select count(*) from bgl", "select count(*) from r2p_bgl_idx where message @@ 'error'", "select count(*) from lh2_bgl", "create table r2p_x(a int)", "delete from bgl where line_no=-1", "drop table bgl", "select count(*) from read_csv('/etc/passwd')", "COPY (select 1) TO '/tmp/r2p_x.csv'", "SET statement_timeout='1s'", "select * from duckdb_settings() limit 1", "select * from pg_roles limit 3"]:
        print("reader:", s, "->", run(rc, s))
except Exception as e: print("reader connect fail", e)
# SET ROLE route
for s in ["SET ROLE r2p_reader", "select current_user", "create table r2p_y(a int)", "RESET ROLE", "select current_user"]:
    print("setrole:", s, "->", run(c, s))
# read-only transaction
c5=psycopg.connect(DSN)
c5.read_only=True
print("RO txn insert ->", run(c5, "create table r2p_z(a int)")); c5.rollback()
