import psycopg
ADMIN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
RO = "host=localhost port=7890 dbname=postgres user=r2_reader password=r2pw"
def run(c, sql):
    try:
        cur = c.execute(sql)
        print("OK ", sql[:110], "->", cur.fetchall()[:3] if cur.description else "")
    except Exception as e:
        print("ERR", sql[:110], "->", type(e).__name__, str(e).splitlines()[0][:160])
with psycopg.connect(ADMIN, autocommit=True) as a:
    run(a, "DROP ROLE IF EXISTS r2_reader")
    run(a, "CREATE ROLE r2_reader LOGIN PASSWORD 'r2pw'")
    run(a, "GRANT SELECT ON bgl TO r2_reader")
    run(a, "GRANT SELECT ON r2_bgl_v TO r2_reader")
    run(a, "GRANT SELECT ON r2_bgl_fts TO r2_reader")
try:
    c = psycopg.connect(RO, autocommit=True)
except Exception as e:
    print("connect as r2_reader failed:", e); raise SystemExit
with c:
    run(c, "select current_user")
    run(c, "select count(*) from bgl")
    run(c, "select count(*) from openstack")
    run(c, "select count(*) from r2_bgl_fts where content @@ 'socket'")
    run(c, "select count(*) from r2_bgl_v")
    run(c, "insert into bgl (line_no) values (-1)")
    run(c, "create table r2_evil (x int)")
    run(c, "drop view r2_bgl_v")
    run(c, "select * from read_csv('/etc/passwd') limit 1")
    run(c, "COPY (select 1) TO '/tmp/r2_x.csv'")
    run(c, "SET default_transaction_read_only = on")
    run(c, "select count(*) from r2_bgl_fts where content @@ 'socket'")
