"""Claim 3c: stability + correctness of plain filters on inverted-index scan."""
import psycopg, datetime as dt
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"
B = "SELECT line_no, ts, BM25(bgl_idx.tableoid) s FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')"
P = " AND ts >= '2005-07-01' AND ts < '2005-08-01'"
T = " AND ts @@ ts_between('2005-07-01 00:00:00'::timestamp,'2005-08-01 00:00:00'::timestamp,true,false)::score(NULL)"
lo, hi = dt.datetime(2005, 7, 1), dt.datetime(2005, 8, 1)
with psycopg.connect(DSN, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
    for run in range(2):
        for k, f in (("none", ""), ("plain", P), ("ts_between", T)):
            cnt = c.execute(f"SELECT count(*) FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed'){f}").fetchone()[0]
            rows = c.execute(f"{B}{f} ORDER BY s DESC, line_no").fetchall()
            out = sum(1 for r in rows if not (lo <= r[1] < hi))
            print(f"run{run} [{k}] count(*)={cnt} rows={len(rows)} out_of_range={out} top={rows[0][0]},{rows[0][2]:.4f} min={min(r[2] for r in rows):.4f} max={max(r[2] for r in rows):.4f}", flush=True)
    truth = c.execute("SELECT count(*) FROM (SELECT line_no FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')) x JOIN bgl USING (line_no) WHERE bgl.ts >= '2005-07-01' AND bgl.ts < '2005-08-01'").fetchone()
    print("truth via join to base table:", truth)
    for extra in (" AND line_no = 3040656", " AND line_no = 2907969"):
        for k, f in (("none", ""), ("plain", P), ("ts_between", T)):
            print(f"{extra.strip()} [{k}]", c.execute(f"{B}{f}{extra}").fetchall())
    print("plain line_no alone:", c.execute(f"{B} AND line_no >= 3040656 AND line_no <= 3040656").fetchall())
    print("EXPLAIN plain+line_no:"); [print("  ", r[0]) for r in c.execute(f"EXPLAIN {B}{P} AND line_no = 3040656").fetchall()]
