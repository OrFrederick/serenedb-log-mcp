"""Minimal repro (deliberation #2): server-side bound LIMIT on a BM25-ordered inverted-index search.
Run each variant in a fresh connection; report OK / ERROR / CONNECTION LOST (crash)."""
import psycopg, sys, time
DSN = "host=localhost port=7890 dbname=postgres user=postgres"
Q = "SELECT line_no, BM25(r2p_bgl_idx.tableoid) s FROM r2p_bgl_idx WHERE message @@ 'error' ORDER BY s DESC, line_no LIMIT {lim}"
variants = [("literal LIMIT 20, server binding", Q.format(lim="20"), None, None),
            ("bound LIMIT %s, server binding (psycopg default)", Q.format(lim="%s"), (20,), None),
            ("bound LIMIT %s, ClientCursor", Q.format(lim="%s"), (20,), psycopg.ClientCursor),
            ("bound LIMIT %s, ORDER BY line_no only (no BM25), server binding",
             "SELECT line_no FROM r2p_bgl_idx WHERE message @@ 'error' ORDER BY line_no LIMIT %s", (20,), None)]
for name, sql, params, cf in variants:
    for attempt in range(30):
        try: psycopg.connect(DSN, connect_timeout=3).close(); break
        except Exception: time.sleep(2)
    t = time.time()
    try:
        with psycopg.connect(DSN, cursor_factory=cf or psycopg.Cursor, autocommit=True) as c:
            n = len(c.execute(sql, params).fetchall())
        print(f"OK     {time.time()-t:6.2f}s rows={n:3d}  {name}")
    except psycopg.OperationalError as e:
        print(f"CRASH? {time.time()-t:6.2f}s  {name}: {str(e).splitlines()[0][:100]}")
    except Exception as e:
        print(f"ERROR  {time.time()-t:6.2f}s  {name}: {type(e).__name__}: {str(e).splitlines()[0][:100]}")
# variants from the original bind_test.py / search shapes (bound query text, prepare, binary, ts_between)
more = [("bound @@ %s + BM25 order + bound LIMIT, prepare=True",
         "select line_no from r2p_bgl_idx where message @@ %s order by BM25(r2p_bgl_idx.tableoid) desc limit %s", ['error', 5], dict(prepare=True)),
        ("same, binary=True", "select line_no from r2p_bgl_idx where message @@ %s order by BM25(r2p_bgl_idx.tableoid) desc limit %s", ['error', 5], dict(binary=True)),
        ("same, default", "select line_no from r2p_bgl_idx where message @@ %s order by BM25(r2p_bgl_idx.tableoid) desc limit %s", ['error', 5], {}),
        ("count with bound ts_between(...)::score(NULL)",
         "select count(*) from r2p_bgl_idx where message @@ %s and ts @@ ts_between(%s::timestamp, %s::timestamp, true, false)::score(NULL)",
         ['error', '2005-06-01', '2005-07-01'], {}),
        ("count with bound ts_between, prepare=True",
         "select count(*) from r2p_bgl_idx where message @@ %s and ts @@ ts_between(%s::timestamp, %s::timestamp, true, false)::score(NULL)",
         ['error', '2005-06-01', '2005-07-01'], dict(prepare=True))]
for name, sql, params, kw in more:
    for attempt in range(30):
        try: psycopg.connect(DSN, connect_timeout=3).close(); break
        except Exception: time.sleep(2)
    t = time.time()
    try:
        with psycopg.connect(DSN, autocommit=True) as c:
            n = c.execute(sql, params, **kw).fetchall()
        print(f"OK     {time.time()-t:6.2f}s {str(n[:2])[:40]:40s} {name}")
    except psycopg.OperationalError as e:
        print(f"CRASH? {time.time()-t:6.2f}s  {name}: {str(e).splitlines()[0][:100]}")
    except Exception as e:
        print(f"ERROR  {time.time()-t:6.2f}s  {name}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
