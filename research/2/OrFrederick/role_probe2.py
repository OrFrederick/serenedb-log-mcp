import psycopg
RO = "host=localhost port=7890 dbname=postgres user=r2_reader password=r2pw"
def run(c, sql):
    try:
        cur = c.execute(sql)
        print("OK ", sql[:110], "->", cur.fetchall()[:2] if cur.description else "")
    except Exception as e:
        print("ERR", sql[:110], "->", type(e).__name__, str(e).splitlines()[0][:160])
with psycopg.connect(RO, autocommit=True) as c:
    run(c, "select name, value from sdb_settings() where name in ('enable_external_access','lock_configuration','allowed_directories','allowed_paths')")
    run(c, "SET enable_external_access = false")
    run(c, "select * from read_csv('/etc/passwd') limit 1")
    run(c, "SET enable_external_access = true")
    run(c, "select count(*) from r2_bgl_fts where content @@ 'socket'")
    run(c, "SET lock_configuration = true")
    run(c, "SET statement_timeout = 5")
with psycopg.connect(RO, autocommit=True) as c:
    run(c, "select current_setting('enable_external_access')")
