"""Log in as each per-Role LOGIN role and probe what it can read."""
import psycopg, time
PROBES = {
 "eng_v":  "SELECT count(*) FROM r4x_eng_v",
 "eng_v_msg": "SELECT message FROM r4x_eng_v WHERE level='FATAL' LIMIT 1",
 "lead_v": "SELECT component, count(*) FROM r4x_lead_v GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
 "mgr_v":  "SELECT component, sum(entries) e, max(nodes) FROM r4x_mgr_v GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
 "base_copy": "SELECT count(*) FROM r4x_bgl",
 "shared_bgl": "SELECT raw FROM bgl LIMIT 1",
 "shared_openstack": "SELECT raw FROM openstack LIMIT 1",
 "write": "CREATE TABLE r4x_evil(x int)",
}
for role in ["r4x_engineer","r4x_lead","r4x_manager"]:
    for name, sql in PROBES.items():
        try:
            with psycopg.connect(f"host=localhost port=7890 dbname=postgres user={role} password=x", autocommit=True) as c:
                t=time.time(); r=c.execute(sql).fetchall()
                print(f"{role:13} {name:16} OK   {str(r)[:70]} ({time.time()-t:.3f}s)")
        except Exception as e:
            print(f"{role:13} {name:16} DENY {str(e).splitlines()[0][:90]}")
