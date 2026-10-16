"""Materialise derived columns once (load-time) to test the speedup for top_patterns/latency."""
import time, psycopg
from bench import MASK_BGL, ENDPOINT, LAT, DSN
c = psycopg.connect(DSN, autocommit=True)
for sql in [
    "DROP TABLE IF EXISTS r2p_os_http", "DROP TABLE IF EXISTS r2p_bgl_pat",
    f"CREATE TABLE r2p_os_http AS SELECT line_no, ts, source_file, request_id, {ENDPOINT} AS endpoint, {LAT} AS latency_s, regexp_extract(message, 'status: ([0-9]+)', 1)::INT AS status FROM openstack WHERE message LIKE '%time: %'",
    f"CREATE TABLE r2p_bgl_pat AS SELECT line_no, ts, node, component, level, {MASK_BGL} AS pattern FROM bgl",
    "SELECT count(*), count(DISTINCT pattern) FROM r2p_bgl_pat", "SELECT count(*), count(DISTINCT endpoint) FROM r2p_os_http",
]:
    t = time.perf_counter(); cur = c.execute(sql)
    print(f"{(time.perf_counter()-t)*1000:9.0f} ms  {sql[:90]}  {cur.fetchall() if cur.description else ''}")
