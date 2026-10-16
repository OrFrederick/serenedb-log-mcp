"""Add Burst counts per problem group: a Burst = maximal run of a group's entries with gaps <= 300 s (glossary candidate)."""
from r4e_common import *
c = conn()
cols, rows, dt = run(c, """CREATE OR REPLACE TABLE r4e_bursts AS
WITH a AS (SELECT dataset, kind, gkey, ts,
             CASE WHEN date_diff('second', lag(ts) OVER (PARTITION BY dataset, kind, gkey ORDER BY ts), ts) <= 300 THEN 0 ELSE 1 END nb
           FROM r4e_pg)
SELECT dataset, kind, gkey, sum(nb) bursts FROM a GROUP BY 1,2,3""")
print("bursts table", round(dt, 2), "s")
print(md_table(*run(c, "SELECT f.gkey, f.n, b.bursts, round(f.n / b.bursts::DOUBLE, 1) entries_per_burst FROM r4e_factors f JOIN r4e_bursts b USING (dataset, kind, gkey) WHERE f.kind='alert_tag' ORDER BY f.n DESC LIMIT 15")[:2]))
