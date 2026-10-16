"""Does a Role need a grant on the inverted index relation, and does it bypass the base-table denial?"""
import psycopg
ADMIN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
def run(c, s):
    try:
        cur = c.execute(s); r = cur.fetchall() if cur.description else None
        print('OK   ', s[:110], '->', r[:3] if r else '')
    except Exception as e: print('ERR  ', s[:110], '->', str(e).strip().splitlines()[0])
def role(r): return psycopg.connect(f"host=localhost port=7890 dbname=postgres user=r4_{r} password=r4_{r}_pw", autocommit=True)
with psycopg.connect(ADMIN, autocommit=True) as c:
    # NOTE: on 26.09.1.3 the documented `AS split_text(...)` form is a syntax error; the PRAGMA-style form works.
    run(c, "DROP INDEX IF EXISTS r4_bgl_idx")
    run(c, "CREATE INDEX r4_bgl_idx ON r4_bgl USING inverted (line_no, content r4_dict) INCLUDE (ts, node)")
    run(c, "VACUUM (REFRESH_TABLE) r4_bgl")
    run(c, "SELECT count(*) FROM r4_bgl_idx WHERE content @@ 'panic'")
with role('engineer') as c:
    print('[engineer, before grant on index]'); run(c, "SELECT count(*) FROM r4_bgl_idx WHERE content @@ 'panic'")
with psycopg.connect(ADMIN, autocommit=True) as c:
    run(c, "GRANT SELECT ON r4_bgl_idx TO r4_engineer")
with role('engineer') as c:
    print('[engineer, after grant on index]'); run(c, "SELECT line_no, ts, node FROM r4_bgl_idx WHERE content @@ 'panic' LIMIT 2")
    run(c, "SELECT count(*) FROM r4_bgl")
with role('manager') as c:
    print('[manager]'); run(c, "SELECT count(*) FROM r4_bgl_idx WHERE content @@ 'panic'")
