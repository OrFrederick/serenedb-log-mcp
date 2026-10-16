"""Inverted index over the Engineer VIEW: is it usable by r4_engineer and denied to others?"""
import psycopg, time
ADMIN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
def run(c, s):
    t=time.time()
    try:
        cur = c.execute(s); r = cur.fetchall() if cur.description else None
        print('OK   ', s[:110], '->', r[:3] if r else '', f'({(time.time()-t)*1000:.0f} ms)')
    except Exception as e: print('ERR  ', s[:110], '->', str(e).strip().splitlines()[0])
def role(r): return psycopg.connect(f"host=localhost port=7890 dbname=postgres user=r4_{r} password=r4_{r}_pw", autocommit=True)
with psycopg.connect(ADMIN, autocommit=True) as c:
    run(c, "DROP INDEX IF EXISTS r4_veng_idx")
    run(c, "CREATE INDEX r4_veng_idx ON r4_v_engineer USING inverted (line_no, content r4_dict) INCLUDE (ts, node)")
    run(c, "SELECT count(*) FROM r4_veng_idx WHERE content @@ 'panic'")
for r in ('engineer', 'manager'):
    with role(r) as c:
        print(f'[{r}]'); run(c, "SELECT line_no, ts, node FROM r4_veng_idx WHERE content @@ 'panic' ORDER BY line_no LIMIT 3")
