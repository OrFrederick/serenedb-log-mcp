"""Claim 3d: plain ts range combined with another scalar filter -> wrong rows?"""
import psycopg, datetime as dt
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"
lo, hi = dt.datetime(2005, 7, 1), dt.datetime(2005, 8, 1)
B = "SELECT line_no, ts, level, node, BM25(bgl_idx.tableoid) s FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')"
P = " AND ts >= '2005-07-01' AND ts < '2005-08-01'"
T = " AND ts @@ ts_between('2005-07-01 00:00:00'::timestamp,'2005-08-01 00:00:00'::timestamp,true,false)::score(NULL)"
with psycopg.connect(DSN, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
    for extra in ("", " AND level = 'FATAL'", " AND line_no IN (3040656, 2907969, 184955)", " AND line_no = 3040656", " AND line_no > 3000000"):
        for k, f in (("plain", P), ("ts_between", T)):
            rows = c.execute(f"{B}{f}{extra} ORDER BY s DESC, line_no").fetchall()
            out = [r[0] for r in rows if not (lo <= r[1] < hi)]
            sc = sorted({round(r[4], 3) for r in rows}, reverse=True)[:4]
            print(f"[{k}]{extra or ' (ts only)'}: rows={len(rows)} out_of_range={len(out)} {out[:5]} scores={sc}", flush=True)
    print("plain level only:", sorted({round(r[4],3) for r in c.execute(f"{B} AND level = 'FATAL'").fetchall()}, reverse=True)[:4])
    print("level::score(NULL)? :", end=" ")
    try: print(sorted({round(r[4],3) for r in c.execute(f"{B} AND level @@ 'FATAL'::score(NULL)").fetchall()}, reverse=True)[:4])
    except Exception as e: print(type(e).__name__, str(e)[:200])
