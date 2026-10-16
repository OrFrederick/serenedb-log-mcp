import psycopg
c=psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
for sql,p in [
 ("select count(*) from r2p_bgl_idx where message @@ %s and component = ANY(%s::varchar[])", ['error', ['APP','KERNEL']]),
 ("select count(*) from r2p_bgl_idx where message @@ %s and list_contains(%s::varchar[], component)", ['error', ['APP','KERNEL']]),
 ("select count(*) from r2p_bgl_idx where message @@ %s and component IN (%s, %s)", ['error', 'APP','KERNEL']),
 ("explain select count(*) from r2p_bgl_idx where message @@ %s and component = ANY(%s::varchar[])", ['error', ['APP','KERNEL']]),
 ("select count(*) from r2p_bgl_idx where message @@ %s", ['ciod']),
 ("select count(*) from bgl where regexp_matches(lower(message), '\\bciod\\b')", []),
 ("select count(*) from bgl where message ilike %s", ['%ciod%']),
 ("select count(*) from r2p_bgl_idx where message @@ ts_phrase(%s)", ['kernel panic']),
 ("select count(*) from bgl where lower(message) like %s", ['%kernel panic%']),
]:
    try: print("OK", sql[:90], p, c.execute(sql,p).fetchall()[:20])
    except Exception as e: print("FAIL", sql[:90], str(e).splitlines()[0][:200])
