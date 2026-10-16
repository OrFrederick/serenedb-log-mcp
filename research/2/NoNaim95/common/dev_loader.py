#!/usr/bin/env python3
"""Throwaway DEV loader for research only (NOT the official organiser loader).

Loads raw BGL (Zenodo 8196385) and OpenStack (normal1, normal2, abnormal) into SereneDB,
plus Loghub-2.0 structured template annotations as separate tables.

Tables:
  bgl(line_no, alert_tag, epoch, ts, node, component, level, message, raw)
     alert_tag: '-' for non-alert, otherwise the alert category (Oliner & Stearley labels)
  openstack(line_no, source_file, log_file, ts, pid, level, component, request_id,
            instance_id, message, raw)
  lh2_bgl(line_id, content, event_id, event_template)
  lh2_openstack(line_id, content, event_id, event_template)
"""
import csv, io, os, re, sys, time
from datetime import datetime
import psycopg

DATA = os.environ.get("DATA", "/Users/deni/hackathon/data")
DSN = os.environ.get("DATABASE_URL", "host=localhost port=7890 dbname=postgres user=postgres")

def copy_rows(conn, table, cols, rows, batch=200_000):
    n = 0
    with conn.cursor() as cur:
        buf = []
        def flush():
            with cur.copy(f"COPY {table} ({','.join(cols)}) FROM STDIN") as cp:
                for r in buf:
                    cp.write_row(r)
        for r in rows:
            buf.append(r); n += 1
            if len(buf) >= batch:
                flush(); buf.clear()
        if buf:
            flush()
    conn.commit()
    return n

def bgl_rows():
    with open(f"{DATA}/raw/BGL.log", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            line = line.rstrip("\n").replace("\x00", "\\x00")
            p = line.split(" ", 9)
            ts = None
            if len(p) >= 5:
                try:
                    ts = datetime.strptime(p[4], "%Y-%m-%d-%H.%M.%S.%f")
                except ValueError:
                    ts = None
            def g(k):
                return p[k] if len(p) > k else None
            epoch = int(p[1]) if len(p) > 1 and p[1].isdigit() else None
            yield (i, g(0), epoch, ts, g(3), g(7), g(8), g(9), line)

OS_RE = re.compile(r"^(\S+) (\S+ \S+) (\d+) (\S+) (\S+) (?:\[(req-[0-9a-f-]+)[^\]]*\]|\[-\])? ?(.*)$")
INST_RE = re.compile(r"\[instance: ([0-9a-f-]{36})\]")

def os_rows():
    n = 0
    for fname in ("openstack_normal1.log", "openstack_normal2.log", "openstack_abnormal.log"):
        with open(f"{DATA}/raw/{fname}", encoding="utf-8", errors="replace") as f:
            for line in f:
                n += 1
                line = line.rstrip("\n").replace("\x00", "\\x00")
                m = OS_RE.match(line)
                if not m:
                    yield (n, fname, None, None, None, None, None, None, None, line, line); continue
                logf, dt, pid, lvl, comp, req, msg = m.groups()
                try:
                    ts = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S.%f")
                except ValueError:
                    ts = None
                im = INST_RE.search(msg)
                yield (n, fname, logf, ts, int(pid), lvl, comp, req, im.group(1) if im else None, msg, line)

def lh2_rows(path):
    csv.field_size_limit(10**9)
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f); next(r)
        for row in r:
            yield (int(row[0]), row[1], row[2], row[3])

DDL = {
 "bgl": "CREATE TABLE bgl (line_no BIGINT, alert_tag VARCHAR, epoch BIGINT, ts TIMESTAMP, node VARCHAR, component VARCHAR, level VARCHAR, message VARCHAR, raw VARCHAR)",
 "openstack": "CREATE TABLE openstack (line_no BIGINT, source_file VARCHAR, log_file VARCHAR, ts TIMESTAMP, pid BIGINT, level VARCHAR, component VARCHAR, request_id VARCHAR, instance_id VARCHAR, message VARCHAR, raw VARCHAR)",
 "lh2_bgl": "CREATE TABLE lh2_bgl (line_id BIGINT, content VARCHAR, event_id VARCHAR, event_template VARCHAR)",
 "lh2_openstack": "CREATE TABLE lh2_openstack (line_id BIGINT, content VARCHAR, event_id VARCHAR, event_template VARCHAR)",
}
COLS = {
 "bgl": ["line_no","alert_tag","epoch","ts","node","component","level","message","raw"],
 "openstack": ["line_no","source_file","log_file","ts","pid","level","component","request_id","instance_id","message","raw"],
 "lh2_bgl": ["line_id","content","event_id","event_template"],
 "lh2_openstack": ["line_id","content","event_id","event_template"],
}
SRC = {
 "bgl": bgl_rows, "openstack": os_rows,
 "lh2_bgl": lambda: lh2_rows(f"{DATA}/lh2/BGL/BGL_full.log_structured.csv"),
 "lh2_openstack": lambda: lh2_rows(f"{DATA}/lh2/OpenStack/OpenStack_full.log_structured.csv"),
}

if __name__ == "__main__":
    tables = sys.argv[1:] or list(DDL)
    with psycopg.connect(DSN, autocommit=False) as conn:
        for t in tables:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {t}"); cur.execute(DDL[t])
            conn.commit()
            t0 = time.time()
            n = copy_rows(conn, t, COLS[t], SRC[t]())
            print(f"{t}: {n} rows in {time.time()-t0:.1f}s", flush=True)
