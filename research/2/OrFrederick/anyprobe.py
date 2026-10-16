import psycopg, time
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
L = ['FATAL','FAILURE']
for name, sql, p in [
  ("any", "select count(*) from bgl where level = ANY(%s)", (L,)),
  ("list_contains", "select count(*) from bgl where list_contains(%s::text[], level)", (L,)),
  ("in_expanded", "select count(*) from bgl where level IN (%s, %s)", tuple(L)),
  ("in_expanded_idx", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s) and level IN (%s, %s)", ('parity',)+tuple(L)),
  ("list_contains_idx", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s) and list_contains(%s::text[], level)", ('parity', L)),
  ("prepared_true", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s) and ts >= %s", ('parity', '2005-09-01')),
  ("injection_attempt", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s)", ("x') or 1=1 --",)),
  ("bad_query_syntax_to_tsquery", "select count(*) from r2_bgl_fts where content @@ to_tsquery(%s)", ('(parity AND',)),
  ("bad_query_syntax_websearch", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s)", ('(parity AND "unclosed',)),
]:
    t=time.perf_counter()
    try:
        r = c.execute(sql, p, prepare=(name=="prepared_true")).fetchone()
        print(f"{name}: {r} {1000*(time.perf_counter()-t):.0f} ms")
    except Exception as e:
        print(f"{name}: ERR {type(e).__name__}: {str(e).splitlines()[0][:150]}")
