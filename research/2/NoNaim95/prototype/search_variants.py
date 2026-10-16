"""Which part of search_logs costs time? (literal LIMIT; median of 3)."""
import psycopg, time, statistics, datetime as dt
c=psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
T0,T1=dt.datetime(2005,6,1),dt.datetime(2006,2,1)
base="FROM r2p_bgl_idx WHERE message @@ {q}{f} ORDER BY score DESC, line_no LIMIT 20"
V={
 "or_noflt_nohl": ("SELECT line_no, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="%s",f=""), ["ciod failed"]),
 "and_noflt_nohl": ("SELECT line_no, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=""), ["ciod failed"]),
 "and_comp_nohl": ("SELECT line_no, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=" AND component IN (%s)"), ["ciod failed","APP"]),
 "and_ts_nohl": ("SELECT line_no, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=" AND ts >= %s AND ts < %s"), ["ciod failed",T0,T1]),
 "and_ts_nohl_inclcols": ("SELECT line_no, ts, node, component, level, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=" AND ts >= %s AND ts < %s"), ["ciod failed",T0,T1]),
 "and_ts_message_col": ("SELECT line_no, message, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=" AND ts >= %s AND ts < %s"), ["ciod failed",T0,T1]),
 "and_ts_hl": ("SELECT line_no, ts_highlight(r2p_bgl_idx.message) h, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=" AND ts >= %s AND ts < %s"), ["ciod failed",T0,T1]),
 "and_noflt_hl": ("SELECT line_no, ts_highlight(r2p_bgl_idx.message) h, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=""), ["ciod failed"]),
 "and_ts_tsbetween": ("SELECT line_no, BM25(r2p_bgl_idx.tableoid) score "+base.format(q="websearch_to_tsquery(%s)",f=" AND ts @@ ts_between(%s, %s, true, false)::score(NULL)"), ["ciod failed",T0,T1]),
 "twostep_hl_join": ("""WITH t AS (SELECT line_no, BM25(r2p_bgl_idx.tableoid) score """+base.format(q="websearch_to_tsquery(%s)",f=" AND ts >= %s AND ts < %s")+""")
     SELECT t.line_no, t.score, b.ts, b.node, b.component, b.level, b.message FROM t JOIN bgl b USING (line_no) ORDER BY t.score DESC, t.line_no""", ["ciod failed",T0,T1]),
}
for k,(sql,p) in V.items():
    ts=[]
    try:
        for _ in range(3):
            t=time.perf_counter(); r=c.execute(sql,p).fetchall(); ts.append((time.perf_counter()-t)*1000)
        print(f"{k:24s} median {statistics.median(ts):8.1f} ms runs={[round(x) for x in ts]} rows={len(r)} first={str(r[0])[:100] if r else None}")
    except Exception as e: print(k, "ERR", str(e).splitlines()[0][:200])
