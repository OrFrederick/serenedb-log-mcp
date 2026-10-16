"""psycopg Connection.read_only=True (emits BEGIN READ ONLY) against SereneDB: FTS + aggregation work, writes fail."""
import time, psycopg
DSN = "host=localhost port=7890 dbname=postgres user=postgres"
adm = psycopg.connect(DSN, autocommit=True)
adm.execute("CREATE TABLE IF NOT EXISTS r2s_t(a INT)")
c = psycopg.connect(DSN)
c.read_only = True
for label, sql, p in [
    ("fts count", "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ %s", ("error",)),
    ("agg by component", "SELECT component, count(*) n FROM bgl GROUP BY 1 ORDER BY n DESC LIMIT 3", None),
    ("time_bucket trend", "SELECT time_bucket(INTERVAL '1 month', ts) b, count(*) FROM bgl GROUP BY 1 ORDER BY 1 LIMIT 3", None),
    ("insert", "INSERT INTO r2s_t VALUES (1)", None),
]:
    t = time.perf_counter()
    try:
        with c.transaction():
            r = c.execute(sql, p).fetchall() if not sql.startswith("INSERT") else c.execute(sql, p)
        print(f"OK  {time.perf_counter()-t:.3f}s {label}: {r if isinstance(r, list) else 'executed'}")
    except Exception as e:
        print(f"ERR {time.perf_counter()-t:.3f}s {label}: {type(e).__name__}: {str(e)[:160]}")
print("rows in r2s_t:", adm.execute("SELECT count(*) FROM r2s_t").fetchone())
adm.execute("DROP TABLE r2s_t")
