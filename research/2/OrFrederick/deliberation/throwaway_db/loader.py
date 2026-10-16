"""Throwaway dev loader for research (NOT the official loader).

Loads raw Zenodo 8196385 BGL.log and OpenStack logs into SereneDB tables
`bgl` and `openstack`. Usage: python dev_loader.py [bgl|openstack|all]
"""
import re, sys, time, psycopg
from pathlib import Path

RAW = Path("/Users/frederick/Development/LogSearcher/data/raw")
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"

BGL_DDL = """CREATE TABLE bgl (
  line_no BIGINT, alert_label TEXT, epoch BIGINT, ts TIMESTAMP, node TEXT,
  rack TEXT, midplane TEXT, msg_type TEXT, component TEXT, level TEXT, content TEXT)"""

OS_DDL = """CREATE TABLE openstack (
  line_no BIGINT, source_file TEXT, log_file TEXT, ts TIMESTAMP, pid INT, level TEXT,
  component TEXT, request_id TEXT, instance_id TEXT, http_method TEXT, http_path TEXT,
  http_status INT, latency_s DOUBLE, content TEXT)"""

NODE_RE = re.compile(r"^(R\d+)-(M\d)")


def esc(v):
    if v is None:
        return None
    return v.replace("\x00", "")


def bgl_rows():
    with open(RAW / "BGL.log", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            p = line.rstrip("\n").split(" ", 9)
            p += [""] * (10 - len(p))
            label, epoch, _date, node, tstr, _node2, mtype, comp, level, content = p
            try:
                d, t = tstr[:10], tstr[11:].replace(".", ":", 2)
                ts = f"{d} {t}"
            except Exception:
                ts = None
            m = NODE_RE.match(node)
            rack = m.group(1) if m else None
            mid = f"{m.group(1)}-{m.group(2)}" if m else None
            yield (i, label, int(epoch) if epoch.isdigit() else None, ts, node, rack, mid,
                   mtype, comp, level, esc(content))


OS_RE = re.compile(r"^(\S+) (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) (\d+) (\w+) (\S+) (?:\[([^\]]*)\] )?(.*)$")
REQ_RE = re.compile(r"req-[0-9a-f-]{36}")
INST_RE = re.compile(r"\[instance: ([0-9a-f-]{36})\]")
HTTP_RE = re.compile(r'"(GET|POST|PUT|DELETE|PATCH|HEAD) (\S+) HTTP/[\d.]+" status: (\d+) len: \d+ time: ([\d.]+)')


def os_rows():
    n = 0
    for name in ["openstack_normal1.log", "openstack_normal2.log", "openstack_abnormal.log"]:
        with open(RAW / name, encoding="utf-8", errors="replace") as f:
            for line in f:
                n += 1
                line = line.rstrip("\n")
                m = OS_RE.match(line)
                if not m:
                    yield (n, name, None, None, None, None, None, None, None, None, None, None, None, esc(line))
                    continue
                logf, ts, pid, level, comp, ctx, content = m.groups()
                req = REQ_RE.search(ctx or "")
                inst = INST_RE.search(content)
                h = HTTP_RE.search(content)
                yield (n, name, logf, ts, int(pid), level, comp, req.group(0) if req else None,
                       inst.group(1) if inst else None,
                       h.group(1) if h else None, h.group(2) if h else None,
                       int(h.group(3)) if h else None, float(h.group(4)) if h else None, esc(content))


def load(conn, table, ddl, rows):
    t0 = time.time()
    conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute(ddl)
    n = 0
    with conn.cursor().copy(f"COPY {table} FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)
            n += 1
    print(f"{table}: {n} rows in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    with psycopg.connect(DSN, autocommit=True) as c:
        if what in ("bgl", "all"):
            load(c, "bgl", BGL_DDL, bgl_rows())
        if what in ("openstack", "all"):
            load(c, "openstack", OS_DDL, os_rows())
