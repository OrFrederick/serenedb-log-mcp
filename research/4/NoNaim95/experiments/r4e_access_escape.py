"""Step 4b: escape-hatch probes as r4e_manager (non-superuser, SELECT only on two views)."""
import json
from r4e_access import t, results, as_role  # re-runs setup (idempotent), then probes
m = as_role("r4e_manager", "r4e_mgr_pw")
probes = [
 ("SELECT count(*) FROM glob('/var/lib/serenedb/*')", "list server data dir"),
 ("SELECT * FROM read_text('/etc/hostname')", "read_text arbitrary server file"),
 ("SELECT count(*) FROM read_csv('/var/lib/serenedb/pg_hba.conf')", "read server auth config"),
 ("COPY (SELECT 1 AS x) TO '/tmp/r4e_mgr_copy.csv'", "write a server file"),
 ("SELECT 1 FROM read_parquet('https://public-pme.s3.eu-west-3.amazonaws.com/text_bench/part_000.parquet') LIMIT 1", "remote read (egress)"),
 ("ATTACH ':memory:' AS r4e_mem", "ATTACH"),
 ("SELECT current_setting('hba')", "read hba setting"),
 ("SET hba = 'host all all 0.0.0.0/0 trust'", "change auth as non-superuser"),
]
for sql, note in probes:
    t(m, "r4e_manager", sql, note)
m.close()
json.dump(results, open("r4e_access_results.json", "w"), indent=1)
