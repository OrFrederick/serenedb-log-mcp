"""Claim 1: inverted index directly on base table bgl (no PK) on 26.09.1.3."""
import time, psycopg
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"
SQL = [
    "SELECT version()",
    "SELECT count(*) FROM bgl",
    "DROP TEXT SEARCH DICTIONARY IF EXISTS bgl_dict",
    """CREATE TEXT SEARCH DICTIONARY bgl_dict (TEMPLATE='text', locale='en_US.UTF-8', "case"='lower', stemming=false, accent=false, frequency=true, position=true, "offset"=true, norm=true)""",
    "CREATE INDEX bgl_idx ON bgl USING inverted (line_no, ts, node, rack, component, level, alert_label, content bgl_dict)",
    "SELECT count(*) FROM bgl_idx WHERE content @@ websearch_to_tsquery('error')",
    "SELECT count(*) FROM bgl_idx WHERE content @@ websearch_to_tsquery('ciod failed')",
]
with psycopg.connect(DSN, autocommit=True) as c:
    for s in SQL:
        t = time.time()
        try:
            cur = c.execute(s)
            r = cur.fetchall() if cur.description else cur.statusmessage
            print(f"OK {time.time()-t:.2f}s | {s[:110]} -> {r}")
        except Exception as e:
            print(f"ERR {time.time()-t:.2f}s | {s[:110]} -> {type(e).__name__}: {e}")
