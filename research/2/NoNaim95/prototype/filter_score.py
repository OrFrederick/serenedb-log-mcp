"""Do filters change BM25 scores / counts? Compare plain SQL vs TSQUERY ::score(NULL) filters."""
import psycopg, time, statistics, datetime as dt
c=psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True, cursor_factory=psycopg.ClientCursor)
T0,T1=dt.datetime(2005,7,1),dt.datetime(2005,8,1)
Q="ciod failed"
F={
 "none": ("", []),
 "sql_range": (" AND ts >= %s AND ts < %s", [T0,T1]),
 "tsb_range_scoreNULL": (" AND ts @@ ts_between(%s, %s, true, false)::score(NULL)", [T0,T1]),
 "tsb_range_plain": (" AND ts @@ ts_between(%s, %s, true, false)", [T0,T1]),
 "sql_comp_in": (" AND component IN (%s, %s)", ["APP","KERNEL"]),
 "tsany_comp_scoreNULL": (" AND component @@ ts_any(%s::varchar[])::score(NULL)", [["APP","KERNEL"]]),
 "all_scoreNULL": (" AND ts @@ ts_between(%s, %s, true, false)::score(NULL) AND component @@ ts_any(%s::varchar[])::score(NULL) AND level @@ ts_any(%s::varchar[])::score(NULL)", [T0,T1,["APP"],["FATAL","FAILURE"]]),
 "all_sql": (" AND ts >= %s AND ts < %s AND component IN (%s) AND level IN (%s,%s)", [T0,T1,"APP","FATAL","FAILURE"]),
}
for k,(f,p) in F.items():
    cnt=c.execute("SELECT count(*) FROM r2p_bgl_idx WHERE message @@ websearch_to_tsquery(%s)"+f, [Q]+p).fetchone()[0]
    sql=("SELECT line_no, BM25(r2p_bgl_idx.tableoid) s FROM r2p_bgl_idx WHERE message @@ websearch_to_tsquery(%s)"+f+
         " ORDER BY s DESC, line_no LIMIT 20")
    ts=[]
    for _ in range(3):
        t=time.perf_counter(); r=c.execute(sql,[Q]+p).fetchall(); ts.append((time.perf_counter()-t)*1000)
    print(f"{k:22s} count={cnt:7d} topscore={r[0][1] if r else None} median={statistics.median(ts):7.1f}ms runs={[round(x) for x in ts]}")
