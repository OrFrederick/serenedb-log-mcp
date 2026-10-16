"""Claim 2 repeat: server-side binding x3 per shape, plus prepare=True, plus 8 runs on one conn (auto-prepare)."""
import time, psycopg
from c2_crash import QA, QB, PA, PB, DSN, alive, started_at
for name, q, p in [("a", QA, PA), ("b", QB, PB)]:
    for i in range(3):
        for prep in (None, True):
            t = time.time()
            try:
                with psycopg.connect(DSN, autocommit=True) as c:
                    n = len(c.execute(q, p, prepare=prep).fetchall())
                r = f"OK rows={n}"
            except Exception as e:
                r = f"ERR {type(e).__name__}: {str(e)[:200]}"
            print(f"[{name} run{i} prepare={prep}] {time.time()-t:.2f}s {r} alive={alive()} {started_at()}", flush=True)
    with psycopg.connect(DSN, autocommit=True) as c:
        try:
            for i in range(8):
                c.execute(q, p).fetchall()
            print(f"[{name} 8x same conn] OK alive={alive()} {started_at()}")
        except Exception as e:
            print(f"[{name} 8x same conn] ERR at {i}: {e} alive={alive()} {started_at()}")
