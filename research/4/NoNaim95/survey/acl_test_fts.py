import psycopg, time
tests = [("r4x_engineer","SELECT count(*) FROM r4x_eng_search_v WHERE message @@ 'error'"),
         ("r4x_engineer","EXPLAIN SELECT count(*) FROM r4x_eng_search_v WHERE message @@ 'error'"),
         ("r4x_lead","SELECT component, count(*) FROM r4x_bgl GROUP BY 1 ORDER BY 2 DESC LIMIT 2"),
         ("r4x_lead","SELECT message FROM r4x_bgl LIMIT 1"),
         ("r4x_lead","SELECT * FROM r4x_bgl LIMIT 1"),
         ("r4x_lead","SELECT count(*) FROM r4x_bgl_idx WHERE message @@ 'error'"),
         ("r4x_lead","SELECT line_no FROM r4x_bgl_idx WHERE message @@ 'error' LIMIT 1")]
for role, sql in tests:
    try:
        with psycopg.connect(f"host=localhost port=7890 dbname=postgres user={role}", autocommit=True) as c:
            t=time.time(); r=c.execute(sql).fetchall(); print(role, "OK", str(r)[:300], f"{time.time()-t:.3f}s")
    except Exception as e: print(role, "DENY", sql[:60], "->", str(e).splitlines()[0])
