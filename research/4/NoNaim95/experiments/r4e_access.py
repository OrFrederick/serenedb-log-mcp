"""Step 4: per-Role SereneDB access prototype. Superuser creates views + LOGIN roles + grants; then connects AS each role
and records exactly what is allowed / refused. Never touches grants of shared tables for PUBLIC or other roles."""
import psycopg, time, json
from r4e_common import conn, DSN
results = []
def t(c, who, sql, note=""):
    t0 = time.time()
    try:
        cur = c.execute(sql); rows = cur.fetchall() if cur.description else []
        r = dict(who=who, sql=sql, ok=True, rows=[list(map(str, x)) for x in rows[:5]], nrows=len(rows), s=round(time.time()-t0, 3), note=note)
    except Exception as e:
        r = dict(who=who, sql=sql, ok=False, error=str(e).strip().splitlines()[0], s=round(time.time()-t0, 3), note=note)
    results.append(r); print(("OK  " if r["ok"] else "ERR ") + who + " | " + sql[:110].replace("\n", " ") + " -> " + (str(r.get("rows"))[:160] if r["ok"] else r["error"]))
    return r

su = conn()
for s in ["DROP VIEW IF EXISTS r4e_mgr_counts", "DROP VIEW IF EXISTS r4e_lead_counts", "DROP VIEW IF EXISTS r4e_mgr_risk"]:
    t(su, "postgres", s)
for r in ["r4e_manager", "r4e_lead", "r4e_engineer", "r4e_nopw"]:
    t(su, "postgres", f"DROP ROLE IF EXISTS {r}")
t(su, "postgres", "SELECT has_table_privilege('public', 'bgl', 'SELECT') AS public_has_select_on_bgl", "check PUBLIC first; we must not REVOKE from PUBLIC on a shared table")
# Manager: aggregated counts only — no message, no node, no raw
t(su, "postgres", """CREATE VIEW r4e_mgr_counts AS
  SELECT 'BGL' AS dataset, component, alert_tag AS problem_group, time_bucket(INTERVAL 7 DAY, ts) AS week,
         count(*) AS entries, count(DISTINCT node) AS nodes, count(DISTINCT regexp_extract(node, '^(R[0-9]+)-', 1)) AS racks
  FROM bgl WHERE alert_tag <> '-' GROUP BY 1,2,3,4
  UNION ALL
  SELECT 'OpenStack', component, level, time_bucket(INTERVAL 7 DAY, ts), count(*), count(DISTINCT instance_id), NULL
  FROM openstack WHERE level IN ('ERROR','WARNING','CRITICAL') GROUP BY 1,2,3,4""")
t(su, "postgres", """CREATE VIEW r4e_mgr_risk AS
  SELECT dataset, gkey AS problem_group, n AS entries, nodes, racks, eff_sources, persistence_proxy, trend_ratio, recency_days
  FROM (SELECT *, active_days AS persistence_proxy FROM r4e_factors) f WHERE kind = 'alert_tag'""")
# Team Lead: per-Component/rack/week counts incl. masked pattern text (no raw lines, no node ids)
t(su, "postgres", """CREATE VIEW r4e_lead_counts AS
  SELECT dataset, kind, gkey AS problem_group, component, team, rack, date_trunc('day', ts) AS day, count(*) AS entries, count(DISTINCT node) AS nodes
  FROM r4e_pg GROUP BY ALL""")
t(su, "postgres", "CREATE ROLE r4e_manager LOGIN PASSWORD 'r4e_mgr_pw'")
t(su, "postgres", "CREATE ROLE r4e_lead LOGIN PASSWORD 'r4e_lead_pw'")
t(su, "postgres", "CREATE ROLE r4e_engineer LOGIN PASSWORD 'r4e_eng_pw'")
t(su, "postgres", "CREATE ROLE r4e_nopw LOGIN")
t(su, "postgres", "GRANT SELECT ON r4e_mgr_counts TO r4e_manager")
t(su, "postgres", "GRANT SELECT ON r4e_mgr_risk TO r4e_manager")
t(su, "postgres", "GRANT SELECT ON r4e_lead_counts TO r4e_lead")
t(su, "postgres", "GRANT SELECT ON r4e_mgr_counts TO r4e_lead")
t(su, "postgres", "GRANT SELECT (line_no, ts, node, component, level, alert_tag) ON bgl TO r4e_lead", "column grant: lead may see who/when but not message/raw")
t(su, "postgres", "GRANT SELECT ON bgl TO r4e_engineer")
t(su, "postgres", "GRANT SELECT ON openstack TO r4e_engineer")
t(su, "postgres", "REVOKE ALL ON bgl FROM r4e_manager", "explicit revoke on base table (no-op if never granted)")
t(su, "postgres", "REVOKE ALL ON openstack FROM r4e_manager")
for r in ["r4e_manager", "r4e_lead", "r4e_engineer"]:
    t(su, "postgres", f"""SELECT '{r}' r, has_table_privilege('{r}', 'bgl', 'SELECT') bgl, has_table_privilege('{r}', 'r4e_mgr_counts', 'SELECT') mgr_view,
       has_column_privilege('{r}', 'bgl', 'message', 'SELECT') bgl_message, has_column_privilege('{r}', 'bgl', 'node', 'SELECT') bgl_node""")
t(su, "postgres", "SELECT relname, relacl FROM pg_class WHERE relname IN ('bgl', 'r4e_mgr_counts', 'r4e_lead_counts', 'r4e_mgr_risk')")

def as_role(role, pw):
    try:
        return psycopg.connect(f"host=localhost port=7890 dbname=postgres user={role}" + (f" password={pw}" if pw is not None else ""), autocommit=True)
    except Exception as e:
        results.append(dict(who=role, sql=f"CONNECT password={'<none>' if pw is None else ('<wrong>' if pw=='WRONG' else '<right>')}", ok=False, error=str(e).strip().splitlines()[0]))
        print("ERR connect", role, pw, str(e).strip().splitlines()[0]); return None

# authentication behaviour (container runs SERENEDB_HOST_AUTH_METHOD=trust)
for role, pw in [("r4e_manager", "r4e_mgr_pw"), ("r4e_manager", "WRONG"), ("r4e_nopw", None)]:
    cx = as_role(role, pw)
    if cx:
        results.append(dict(who=role, sql=f"CONNECT password={'<none>' if pw is None else ('<wrong>' if pw=='WRONG' else '<right>')}", ok=True, rows=[], note="connected"))
        print("OK  connect", role, pw); cx.close()

m = as_role("r4e_manager", "r4e_mgr_pw")
t(m, "r4e_manager", "SELECT current_user, session_user")
t(m, "r4e_manager", "SELECT dataset, problem_group, sum(entries) entries FROM r4e_mgr_counts GROUP BY 1,2 ORDER BY 3 DESC LIMIT 5", "allowed: aggregated view")
t(m, "r4e_manager", "SELECT problem_group, entries, nodes, eff_sources, trend_ratio FROM r4e_mgr_risk ORDER BY entries DESC LIMIT 3")
t(m, "r4e_manager", "SELECT count(*) FROM bgl", "base table must be refused")
t(m, "r4e_manager", "SELECT message FROM bgl LIMIT 1")
t(m, "r4e_manager", "SELECT message FROM r4e_mgr_counts LIMIT 1", "no message column in view")
t(m, "r4e_manager", "SELECT * FROM openstack LIMIT 1")
t(m, "r4e_manager", "SELECT count(*) FROM r4e_pg", "my own helper table with masked text: not granted")
t(m, "r4e_manager", "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ 'severed'", "inverted index queried as a relation: does it bypass table grants?")
t(m, "r4e_manager", "SELECT count(*) FROM r4e_lead_counts", "other Role's view")
t(m, "r4e_manager", "CREATE VIEW r4e_mgr_sneak AS SELECT message FROM bgl", "can the manager create a view over bgl?")
t(m, "r4e_manager", "SELECT * FROM r4e_mgr_sneak LIMIT 1")
t(m, "r4e_manager", "CREATE TABLE r4e_mgr_tmp AS SELECT 1 x", "can the manager create tables in public?")
t(m, "r4e_manager", "SELECT definition FROM pg_views WHERE viewname = 'r4e_mgr_counts'", "catalog: view SQL visible (metadata, not data)")
t(m, "r4e_manager", "SELECT * FROM read_csv('/etc/passwd') LIMIT 2", "file-reading table function as non-superuser")
t(m, "r4e_manager", "GRANT SELECT ON bgl TO r4e_manager", "self-escalation")
t(m, "r4e_manager", "SET ROLE postgres", "role switch")
for i in range(3):
    t(m, "r4e_manager", "SELECT problem_group, sum(entries) FROM r4e_mgr_counts WHERE dataset='BGL' GROUP BY 1 ORDER BY 2 DESC LIMIT 3", f"timing run {i+1}: view aggregates 4.7M rows at query time")
t(m, "r4e_manager", "DROP VIEW IF EXISTS r4e_mgr_sneak"); t(m, "r4e_manager", "DROP TABLE IF EXISTS r4e_mgr_tmp")
m.close()

l = as_role("r4e_lead", "r4e_lead_pw")
t(l, "r4e_lead", "SELECT count(*) FROM bgl", "count(*) with column grant only")
t(l, "r4e_lead", "SELECT component, count(*) FROM bgl GROUP BY 1 ORDER BY 2 DESC LIMIT 3", "granted columns")
t(l, "r4e_lead", "SELECT message FROM bgl LIMIT 1", "ungranted column")
t(l, "r4e_lead", "SELECT * FROM bgl LIMIT 1", "SELECT * includes ungranted columns")
t(l, "r4e_lead", "SELECT count(*) FROM bgl WHERE message LIKE '%severed%'", "filter on ungranted column")
t(l, "r4e_lead", "SELECT problem_group, sum(entries) FROM r4e_lead_counts WHERE kind='alert_tag' GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
l.close()
e = as_role("r4e_engineer", "r4e_eng_pw")
t(e, "r4e_engineer", "SELECT left(message, 60) FROM bgl WHERE alert_tag = 'APPSEV' LIMIT 1")
t(e, "r4e_engineer", "SELECT count(*) FROM r2p_bgl_idx WHERE message @@ '\"Link has been severed\"'", "engineer FTS via index relation")
t(e, "r4e_engineer", "SELECT count(*) FROM r4e_mgr_counts", "engineer not granted manager view")
e.close()
json.dump(results, open("r4e_access_results.json", "w"), indent=1)
