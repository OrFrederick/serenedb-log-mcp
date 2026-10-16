"""Drop every r4e_* object (views, roles, tables). Re-create with: r4e_build.py -> r4e_bursts.py -> r4e_rank.py -> r4e_evidence.py -> r4e_access.py -> r4e_access_escape.py
NOTE: do NOT re-run the 'SET enable_external_access = false' probe on a shared server: it disables file/remote access server-wide until restart."""
from r4e_common import conn, run
c = conn()
for s in ["DROP VIEW IF EXISTS r4e_mgr_counts", "DROP VIEW IF EXISTS r4e_mgr_risk", "DROP VIEW IF EXISTS r4e_lead_counts",
          "REVOKE SELECT (line_no, ts, node, component, level, alert_tag) ON bgl FROM r4e_lead", "REVOKE ALL ON bgl FROM r4e_engineer", "REVOKE ALL ON openstack FROM r4e_engineer",
          "DROP ROLE IF EXISTS r4e_manager", "DROP ROLE IF EXISTS r4e_lead", "DROP ROLE IF EXISTS r4e_engineer", "DROP ROLE IF EXISTS r4e_nopw",
          "DROP TABLE IF EXISTS r4e_bursts", "DROP TABLE IF EXISTS r4e_factors", "DROP TABLE IF EXISTS r4e_pg", "DROP TABLE IF EXISTS r4e_clock",
          "DROP TABLE IF EXISTS r4e_ownership_placeholder"]:
    try: run(c, s, quiet=True); print("ok", s)
    except Exception as e: print("ERR", s, str(e).splitlines()[0])
print(run(c, "SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'r4e%'", quiet=True)[1],
      run(c, "SELECT rolname FROM pg_roles WHERE rolname LIKE 'r4e%'", quiet=True)[1])
