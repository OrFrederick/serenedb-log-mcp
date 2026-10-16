"""Claim 3b: per-row score diff plain filter vs ts_between, and score distribution."""
import psycopg
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"
B = "SELECT line_no, ts, BM25(bgl_idx.tableoid) s FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')"
P = " AND ts >= '2005-07-01' AND ts < '2005-08-01'"
T = " AND ts @@ ts_between('2005-07-01 00:00:00'::timestamp,'2005-08-01 00:00:00'::timestamp,true,false)::score(NULL)"
with psycopg.connect(DSN, autocommit=True, cursor_factory=psycopg.ClientCursor) as c:
    for k, f in (("plain", P), ("ts_between", T)):
        rows = c.execute(f"{B}{f} ORDER BY s DESC, line_no").fetchall()
        d = {}
        for r in rows: d[round(r[2], 4)] = d.get(round(r[2], 4), 0) + 1
        print(k, "n=", len(rows), "score histogram:", sorted(d.items(), reverse=True)[:8])
        print("  top5:", rows[:5])
    for ln in (2907969, 3040656, 1577127):
        print(ln, "no filter:", c.execute(f"{B} AND line_no = {ln}").fetchall(),
              "| plain:", c.execute(f"{B}{P} AND line_no = {ln}").fetchall(),
              "| tsb:", c.execute(f"{B}{T} AND line_no = {ln}").fetchall())
        print("   content:", c.execute(f"SELECT content FROM bgl WHERE line_no={ln}").fetchone())
    # plain WHERE with non-ts filter
    print("plain level filter top3:", c.execute(f"{B} AND level = 'FATAL' ORDER BY s DESC, line_no LIMIT 3").fetchall())
    print("subquery-wrapped plain filter top3:", c.execute(f"SELECT * FROM ({B}) x WHERE ts >= '2005-07-01' AND ts < '2005-08-01' ORDER BY s DESC, line_no LIMIT 3").fetchall())
