"""Shared helpers for r4e_* experiments (issue #4). Runs SQL on local SereneDB, logs SQL+timing."""
import time, psycopg, json, statistics
DSN = "host=localhost port=7890 dbname=postgres user=postgres"

def conn(dsn=DSN, wait_s=300):
    # shared dev server SIGSEGVs under concurrent load and is auto-restarted; wait for it
    t0 = time.time()
    while True:
        try:
            return psycopg.connect(dsn, autocommit=True)
        except psycopg.OperationalError:
            if time.time() - t0 > wait_s: raise
            time.sleep(3)

def run(c, sql, params=None, quiet=False):
    t = time.time(); cur = c.execute(sql, params)
    rows = cur.fetchall() if cur.description else []
    cols = [d.name for d in cur.description] if cur.description else []
    dt = time.time() - t
    if not quiet:
        print(f"-- {len(rows)} rows, {dt:.3f}s")
    return cols, rows, dt

# Message mask -> pattern ("masked-message pattern"). RE2 syntax (SereneDB uses RE2; 'g' = global).
def mask_sql(col="message"):
    e = col
    for pat, rep in [
        (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<UUID>"),
        (r"R[0-9]{2}-M[0-9][A-Za-z0-9:\-]*", "<LOC>"),
        (r"/[^ ,;()\]]+", "<PATH>"),
        (r"0x[0-9a-fA-F]+", "<HEX>"),
        (r"\b[0-9a-fA-F]{16,}\b", "<HEX>"),
        (r"[0-9]+", "<N>"),
        (r"\s+", " "),
    ]:
        e = f"regexp_replace({e}, '{pat}', '{rep}', 'g')"
    return f"coalesce(left(trim({e}), 160), '<no message>')"

def md_table(cols, rows, n=None):
    rows = rows if n is None else rows[:n]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(("%.3g" % v) if isinstance(v, float) else str(v).replace("|", "\\|") for v in r) + " |")
    return "\n".join(out)
