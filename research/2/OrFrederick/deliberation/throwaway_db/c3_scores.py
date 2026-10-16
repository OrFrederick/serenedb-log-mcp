"""Claim 3: BM25 score with plain SQL ts filter vs ts_between::score(NULL). Literal SQL (client-side)."""
import time, psycopg
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"
BASE = "FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')"
F = {
    "no filter": "",
    "plain ts >= <": " AND ts >= '2005-07-01' AND ts < '2005-08-01'",
    "ts_between::score(NULL)": " AND ts @@ ts_between('2005-07-01 00:00:00'::timestamp,'2005-08-01 00:00:00'::timestamp,true,false)::score(NULL)",
}
with psycopg.connect(DSN, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
    for k, f in F.items():
        for q in (f"SELECT count(*) {BASE}{f}",
                  f"SELECT line_no, BM25(bgl_idx.tableoid) s {BASE}{f} ORDER BY s DESC, line_no LIMIT 3",
                  f"SELECT max(BM25(bgl_idx.tableoid)), min(BM25(bgl_idx.tableoid)) {BASE}{f}"):
            t = time.time()
            try:
                r = c.execute(q).fetchall()
            except Exception as e:
                r = f"ERR {type(e).__name__}: {str(e)[:250]}"
            print(f"[{k}] {time.time()-t:.2f}s {q[:60]}... -> {r}", flush=True)
    # same line_no scored under each filter
    q = ("SELECT line_no, BM25(bgl_idx.tableoid) s {b}{f} ORDER BY s DESC, line_no LIMIT 1")
    print("SQL used:", {k: f"SELECT line_no, BM25(bgl_idx.tableoid) s {BASE}{f} ORDER BY s DESC, line_no LIMIT 3" for k, f in F.items()})
