"""Per-Role views + LOGIN roles + GRANT on r4_ copies (never touches base tables' ACLs)."""
import psycopg, time
ADMIN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
def run(c, s):
    try:
        cur = c.execute(s); r = cur.fetchall() if cur.description else None
        print('OK   ', s.splitlines()[0][:100], '->', r[:3] if r else '')
    except Exception as e: print('ERR  ', s.splitlines()[0][:100], '->', str(e).strip().splitlines()[0])
with psycopg.connect(ADMIN, autocommit=True) as c:
    for s in """DROP INDEX IF EXISTS r4_bgl_idx
DROP VIEW IF EXISTS r4_v_engineer
DROP VIEW IF EXISTS r4_v_teamlead
DROP VIEW IF EXISTS r4_v_manager
DROP TABLE IF EXISTS r4_bgl
DROP ROLE IF EXISTS r4_engineer
DROP ROLE IF EXISTS r4_teamlead
DROP ROLE IF EXISTS r4_manager""".splitlines(): run(c, s)
    # small slice: last 8 days of BGL
    run(c, """CREATE TABLE r4_bgl (line_no BIGINT PRIMARY KEY, alert_label TEXT, ts TIMESTAMP, node TEXT, rack TEXT, midplane TEXT, component TEXT, level TEXT, content TEXT)""")
    run(c, """INSERT INTO r4_bgl SELECT line_no, alert_label, ts, node, rack, midplane, component, level, content FROM bgl WHERE ts >= TIMESTAMP '2005-12-27 00:00:00'""")
    run(c, "SELECT count(*) FROM r4_bgl")
    # Engineer: raw Log Entries. Team Lead: hourly counts per Component/category/midplane, no content. Manager: daily counts per category with spread, no node ids, no content.
    run(c, "CREATE VIEW r4_v_engineer AS SELECT line_no, ts, node, midplane, component, level, alert_label, content FROM r4_bgl")
    run(c, """CREATE VIEW r4_v_teamlead AS SELECT date_trunc('hour', ts) AS hour, component, level, alert_label, midplane, count(*) AS log_entries, count(DISTINCT node) AS nodes FROM r4_bgl GROUP BY 1,2,3,4,5""")
    run(c, """CREATE VIEW r4_v_manager AS SELECT date_trunc('day', ts) AS day, component, alert_label, count(*) AS log_entries, count(DISTINCT midplane) AS midplanes, count(DISTINCT rack) AS racks FROM r4_bgl WHERE alert_label <> '-' GROUP BY 1,2,3""")
    for r in ('engineer','teamlead','manager'):
        run(c, f"CREATE ROLE r4_{r} LOGIN PASSWORD 'r4_{r}_pw'")
        run(c, f"GRANT SELECT ON r4_v_{r} TO r4_{r}")
    run(c, "CREATE TEXT SEARCH DICTIONARY r4_dict AS split_text(case := 'lower') WITH (frequency, position)")
    run(c, "CREATE INDEX r4_bgl_idx ON r4_bgl USING inverted (line_no, content r4_dict) INCLUDE (ts, node)")
    run(c, "VACUUM (REFRESH_TABLE) r4_bgl")
    run(c, "SELECT count(*) FROM r4_bgl_idx WHERE content @@ 'panic'")
    run(c, "GRANT SELECT ON r4_bgl_idx TO r4_engineer")
    run(c, "SELECT relname, relacl FROM pg_class WHERE relname LIKE 'r4_%'")
print('\n--- acting as each Role (password login over TCP) ---')
tests = {
 'engineer': ["SELECT count(*) FROM r4_v_engineer", "SELECT count(*) FROM r4_bgl", "SELECT count(*) FROM bgl",
              "SELECT count(*) FROM r4_bgl_idx WHERE content @@ 'panic'", "SELECT count(*) FROM r4_v_manager"],
 'teamlead': ["SELECT alert_label, sum(log_entries) FROM r4_v_teamlead WHERE alert_label <> '-' GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
              "SELECT content FROM r4_v_engineer LIMIT 1", "SELECT count(*) FROM r4_bgl", "SELECT count(*) FROM r4_bgl_idx WHERE content @@ 'panic'"],
 'manager':  ["SELECT alert_label, sum(log_entries), max(midplanes) FROM r4_v_manager GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
              "SELECT * FROM r4_v_teamlead LIMIT 1", "SELECT content FROM r4_bgl LIMIT 1", "SELECT count(*) FROM bgl", "CREATE VIEW r4_hack AS SELECT 1"],
}
for r, qs in tests.items():
    try:
        with psycopg.connect(f"host=localhost port=7890 dbname=postgres user=r4_{r} password=r4_{r}_pw", autocommit=True) as c:
            print(f'[{r}] connected as', c.execute('select current_user').fetchone()[0])
            for q in qs: run(c, q)
    except Exception as e: print(f'[{r}] CONNECT FAILED', e)
