"""Re-run of crash_repro.py on native x86_64 SereneDB 26.10.1 (host linuxbox, via SSH tunnel on :7890).

Same 9 variants as crash_repro.py. Differences:
- output is flushed per line (the first attempt lost its output when the client was interrupted);
- before each variant we wait until `select 1` succeeds;
- crash detection is authoritative: the container's StartedAt/RestartCount is read over SSH
  before and after each variant (restart policy is unless-stopped, so a SIGSEGV shows up as a restart).

Usage: python -u crash_repro_linuxbox.py <runs>
"""
import subprocess, sys, time, datetime
import psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 5

Q = "SELECT line_no, BM25(r2p_bgl_idx.tableoid) s FROM r2p_bgl_idx WHERE message @@ 'error' ORDER BY s DESC, line_no LIMIT {lim}"
Q2 = "select line_no from r2p_bgl_idx where message @@ %s order by BM25(r2p_bgl_idx.tableoid) desc limit %s"
Q3 = ("select count(*) from r2p_bgl_idx where message @@ %s and "
      "ts @@ ts_between(%s::timestamp, %s::timestamp, true, false)::score(NULL)")
# (name, sql, params, cursor_factory, execute kwargs)
VARIANTS = [
    ("literal LIMIT 20, server binding", Q.format(lim="20"), None, None, {}),
    ("bound LIMIT %s, server binding (psycopg default)", Q.format(lim="%s"), (20,), None, {}),
    ("bound LIMIT %s, ClientCursor", Q.format(lim="%s"), (20,), psycopg.ClientCursor, {}),
    ("bound LIMIT %s, ORDER BY line_no only (no BM25), server binding",
     "SELECT line_no FROM r2p_bgl_idx WHERE message @@ 'error' ORDER BY line_no LIMIT %s", (20,), None, {}),
    ("bound @@ %s + BM25 order + bound LIMIT, prepare=True", Q2, ['error', 5], None, dict(prepare=True)),
    ("same, binary=True", Q2, ['error', 5], None, dict(binary=True)),
    ("same, default", Q2, ['error', 5], None, {}),
    ("count with bound ts_between(...)::score(NULL)", Q3, ['error', '2005-06-01', '2005-07-01'], None, {}),
    ("count with bound ts_between, prepare=True", Q3, ['error', '2005-06-01', '2005-07-01'], None, dict(prepare=True)),
]


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%SZ")


def server_state():
    out = subprocess.run(
        ["ssh", "linuxbox", "docker", "inspect", "serenedb", "--format",
         "{{.State.StartedAt}} restarts={{.RestartCount}}"],
        capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def sigsegv_count():
    out = subprocess.run(["ssh", "linuxbox", "docker logs serenedb 2>&1 | grep -c 'SIGSEGV received'"],
                         capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def wait_ready(max_s=180):
    t = time.time()
    while time.time() - t < max_s:
        try:
            with psycopg.connect(DSN, connect_timeout=3, autocommit=True) as c:
                c.execute("select 1").fetchall()
            return time.time() - t
        except Exception:
            time.sleep(2)
    raise SystemExit("server not ready after %ss" % max_s)


def main():
    print(f"# {now()} host linuxbox x86_64, image serenedb/serenedb:latest", flush=True)
    with psycopg.connect(DSN, autocommit=True) as c:
        print("# version:", c.execute("select version()").fetchone()[0], flush=True)
    print(f"# start state: {server_state()}  SIGSEGV lines in container log so far: {sigsegv_count()}", flush=True)
    crashes = 0
    for run in range(1, RUNS + 1):
        print(f"## run {run}  {now()}  state: {server_state()}", flush=True)
        for i, (name, sql, params, cf, kw) in enumerate(VARIANTS, 1):
            waited = wait_ready()
            before = server_state()
            t = time.time()
            try:
                with psycopg.connect(DSN, cursor_factory=cf or psycopg.Cursor, autocommit=True) as c:
                    rows = c.execute(sql, params, **kw).fetchall()
                status, detail = "OK    ", f"rows={len(rows):3d} {str(rows[:1])[:28]}"
            except psycopg.OperationalError as e:
                status, detail = "CONNLOST", str(e).splitlines()[0][:90]
            except Exception as e:
                status, detail = "ERROR ", f"{type(e).__name__}: {str(e).splitlines()[0][:90]}"
            dt = time.time() - t
            if status == "CONNLOST":
                wait_ready()
            after = server_state()
            crashed = before != after
            crashes += crashed
            print(f"{now()} r{run} v{i} {status} {dt:6.2f}s {'SERVER-RESTARTED' if crashed else 'server-up':16s} "
                  f"(waited {waited:4.1f}s) {name} | {detail}", flush=True)
    print(f"# end state: {server_state()}  SIGSEGV lines in container log: {sigsegv_count()}  "
          f"crashes detected in this script: {crashes}", flush=True)


if __name__ == "__main__":
    main()
