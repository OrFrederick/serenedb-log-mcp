"""Test psycopg3 server-side parameter binding against SereneDB."""
import psycopg, datetime as dt, traceback
DSN="host=localhost port=7890 dbname=postgres user=postgres"
tests = {
 "plain @@ %s": ("select count(*) from r2p_bgl_idx where message @@ %s", ["ciod"]),
 "@@ websearch_to_tsquery(%s)": ("select count(*) from r2p_bgl_idx where message @@ websearch_to_tsquery(%s)", ['ciod "failed"']),
 "@@ plainto_tsquery(%s)": ("select count(*) from r2p_bgl_idx where message @@ plainto_tsquery(%s)", ['ciod failed']),
 "@@ ts_phrase(%s)": ("select count(*) from r2p_bgl_idx where message @@ ts_phrase(%s)", ['kernel panic']),
 "@@ ts_levenshtein(%s,%s)": ("select count(*) from r2p_bgl_idx where message @@ ts_levenshtein(%s, %s)", ['paritty', 1]),
 "ts >= %s timestamp": ("select count(*) from r2p_bgl_idx where message @@ %s and ts >= %s and ts < %s", ['error', dt.datetime(2005,7,1), dt.datetime(2005,8,1)]),
 "component = ANY(%s)": ("select count(*) from r2p_bgl_idx where message @@ %s and component = ANY(%s)", ['error', ['APP','KERNEL']]),
 "LIMIT %s": ("select line_no from r2p_bgl_idx where message @@ %s order by BM25(r2p_bgl_idx.tableoid) desc limit %s", ['error', 5]),
 "interval %s::interval": ("select time_bucket(%s::interval, ts) b, count(*) from bgl group by b order by b limit 3", ['1 day']),
 "interval py timedelta": ("select time_bucket(%s, ts) b, count(*) from bgl group by b order by b limit 3", [dt.timedelta(days=1)]),
 "OFFSET %s": ("select line_no from bgl where node=%s order by line_no limit %s offset %s", ['R02-M1-N0-C:J12-U11', 3, 2]),
}
for mode in ["server-side(default)", "prepare=True", "binary"]:
    print("=====", mode)
    with psycopg.connect(DSN, autocommit=True) as c:
        for name,(sql,p) in tests.items():
            try:
                kw = {}
                if mode=="prepare=True": kw["prepare"]=True
                if mode=="binary": kw["binary"]=True
                r = c.execute(sql, p, **kw).fetchall()
                print("OK  ", name, r[:3])
            except Exception as e:
                print("FAIL", name, type(e).__name__, str(e).splitlines()[0][:200])
