"""Claim 2: BM25 crash shapes with server-side vs client-side binding vs literals."""
import time, datetime as dt, subprocess, psycopg
DSN = "host=localhost port=7892 dbname=postgres user=postgres password=postgres"

QA = ("SELECT line_no, BM25(bgl_idx.tableoid) s FROM bgl_idx WHERE content @@ websearch_to_tsquery(%s) "
      "ORDER BY s DESC, line_no LIMIT %s")
QB = ("SELECT line_no, BM25(bgl_idx.tableoid) s FROM bgl_idx WHERE content @@ websearch_to_tsquery(%s) "
      "AND ts @@ ts_between(%s,%s,true,false)::score(NULL) ORDER BY s DESC, line_no LIMIT %s")
PA = ["error", 20]
PB = ["error", dt.datetime(2005, 7, 1), dt.datetime(2005, 8, 1), 20]
LA = QA.replace("%s", "'error'", 1).replace("%s", "20", 1)
LB = (QB.replace("%s", "'error'", 1).replace("%s", "'2005-07-01 00:00:00'::timestamp", 1)
      .replace("%s", "'2005-08-01 00:00:00'::timestamp", 1).replace("%s", "20", 1))


def alive():
    for _ in range(30):
        try:
            with psycopg.connect(DSN, connect_timeout=3) as c:
                c.execute("SELECT 1")
                return True
        except Exception:
            time.sleep(2)
    return False


def started_at():
    return subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}} restarts={{.RestartCount}} running={{.State.Running}}", "serenedb-d2"],
                          capture_output=True, text=True).stdout.strip()


if __name__ != "__main__": cases = []
else: cases = [
    ("a server-side", QA, PA, None), ("a client-side", QA, PA, psycopg.ClientCursor), ("a literal", LA, None, None),
    ("b server-side", QB, PB, None), ("b client-side", QB, PB, psycopg.ClientCursor), ("b literal", LB, None, None),
]
for name, q, p, cf in cases:
    t = time.time()
    try:
        with psycopg.connect(DSN, autocommit=True, **({"cursor_factory": cf} if cf else {})) as c:
            rows = c.execute(q, p).fetchall()
        res = f"OK rows={len(rows)} top={rows[:3]}"
    except Exception as e:
        res = f"ERR {type(e).__name__}: {str(e)[:300]}"
    el = time.time() - t
    print(f"[{name}] {el:.2f}s {res}\n   server_alive_after={alive()} container={started_at()}", flush=True)
