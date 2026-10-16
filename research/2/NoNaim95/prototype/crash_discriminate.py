"""Which factor makes the SIGSEGV likely? Follow-up to crash_repro_linuxbox.py (native x86_64, SereneDB 26.10.1).

crash_repro_linuxbox.out showed crashes only in v1-v4 (literal `@@ 'error'`, LIMIT 20) and none in v5-v9
(bound `@@ %s`, LIMIT 5 or count). This script separates: literal vs bound query text, LIMIT 20 vs 5,
BM25 in the select list, and the two SQL shapes search_logs would actually use (sort=time, sort=relevance),
with psycopg server-side binding and with ClientCursor. Variants are interleaved in a seeded random order.

Usage: python -u crash_discriminate.py <rounds>
"""
import random, sys, time
import psycopg
from crash_repro_linuxbox import DSN, now, server_state, sigsegv_count, wait_ready

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
SC = "SELECT line_no, BM25(r2p_bgl_idx.tableoid) s FROM r2p_bgl_idx WHERE message @@ {q} ORDER BY s DESC, line_no LIMIT {lim}"
OB = "SELECT line_no FROM r2p_bgl_idx WHERE message @@ {q} ORDER BY BM25(r2p_bgl_idx.tableoid) DESC LIMIT {lim}"
TOOL_TIME = ("SELECT line_no, ts, node, component, level, message FROM r2p_bgl_idx "
             "WHERE message @@ websearch_to_tsquery(%s) ORDER BY ts, line_no LIMIT %s")
TOOL_REL = ("SELECT line_no, ts, node, component, level, message, BM25(r2p_bgl_idx.tableoid) s FROM r2p_bgl_idx "
            "WHERE message @@ websearch_to_tsquery(%s) ORDER BY s DESC, line_no LIMIT %s")
# key: (description, sql, params, cursor_factory)
VARIANTS = {
    "A": ("literal 'error', BM25 selected, literal LIMIT 20 (= v1)", SC.format(q="'error'", lim="20"), None, None),
    "B": ("bound 'error', BM25 selected, bound LIMIT 20", SC.format(q="%s", lim="%s"), ("error", 20), None),
    "C": ("literal 'error', BM25 only in ORDER BY, literal LIMIT 5", OB.format(q="'error'", lim="5"), None, None),
    "D": ("bound 'error', BM25 only in ORDER BY, bound LIMIT 20", OB.format(q="%s", lim="%s"), ("error", 20), None),
    "E": ("tool shape sort=time, websearch_to_tsquery(%s), LIMIT %s=20, server binding", TOOL_TIME, ("error", 20), None),
    "F": ("tool shape sort=time, same, ClientCursor", TOOL_TIME, ("error", 20), psycopg.ClientCursor),
    "G": ("tool shape sort=relevance, websearch_to_tsquery(%s), LIMIT %s=20, server binding", TOOL_REL, ("error", 20), None),
    "H": ("tool shape sort=relevance, same, ClientCursor", TOOL_REL, ("error", 20), psycopg.ClientCursor),
}


def main():
    rng = random.Random(42)
    print(f"# {now()} crash_discriminate.py, {ROUNDS} rounds, seed 42", flush=True)
    for k, (d, sql, p, cf) in VARIANTS.items():
        print(f"# {k}: {d}{' [ClientCursor]' if cf else ''}\n#    {sql}  params={p}", flush=True)
    print(f"# start state: {server_state()}  SIGSEGV lines so far: {sigsegv_count()}", flush=True)
    tally = {k: [0, 0] for k in VARIANTS}  # [crashes, runs]
    for rnd in range(1, ROUNDS + 1):
        order = list(VARIANTS)
        rng.shuffle(order)
        for k in order:
            d, sql, params, cf = VARIANTS[k]
            wait_ready()
            before = server_state()
            t = time.time()
            try:
                with psycopg.connect(DSN, cursor_factory=cf or psycopg.Cursor, autocommit=True) as c:
                    rows = c.execute(sql, params).fetchall()
                status, detail = "OK      ", f"rows={len(rows)} first={rows[0][0]}"
            except psycopg.OperationalError as e:
                status, detail = "CONNLOST", str(e).splitlines()[0][:70]
                wait_ready()
            except Exception as e:
                status, detail = "ERROR   ", f"{type(e).__name__}: {str(e).splitlines()[0][:90]}"
            dt = time.time() - t
            crashed = server_state() != before
            tally[k][0] += crashed
            tally[k][1] += 1
            print(f"{now()} round {rnd} {k} {status} {dt:5.2f}s {'SERVER-RESTARTED' if crashed else 'server-up'} | {detail}",
                  flush=True)
    print(f"# end state: {server_state()}  SIGSEGV lines: {sigsegv_count()}", flush=True)
    for k, (c, n) in tally.items():
        print(f"# {k}: {c}/{n} crashed  {VARIANTS[k][0]}{' [ClientCursor]' if VARIANTS[k][3] else ''}", flush=True)


if __name__ == "__main__":
    main()
