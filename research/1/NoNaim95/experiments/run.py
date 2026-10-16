#!/usr/bin/env python3
"""Runner for r1e_ experiments (issue #1, incident model).

Usage: run.py [--repeat N] [--maxrows M] file.sql [file2.sql ...]

Splits each file on lines ending with ';' (statements separated by a line that
ends in ';'), runs every statement against local SereneDB, repeats each
statement N times (default 3; CREATE OR REPLACE is idempotent so repeats are
safe) and records the median wall time. Output goes to outputs/<file>.out.
Statements preceded by '-- @once', and all DDL/INSERT statements, run only once
(repeated CTAS on the shared server coincided with SIGSEGV crashes).
"""
import statistics
import sys
import time
from pathlib import Path

import psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"
HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
OUT.mkdir(exist_ok=True)


def split_statements(text):
    stmts, buf, once = [], [], False
    for line in text.splitlines():
        if line.strip() == "-- @once":
            once = True
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            sql = "\n".join(buf).strip()
            body = "\n".join(l for l in buf if not l.strip().startswith("--")).strip()
            if body:
                stmts.append((sql.rstrip(";"), once))
            buf, once = [], False
    rest = "\n".join(l for l in buf if not l.strip().startswith("--")).strip()
    if rest:
        stmts.append(("\n".join(buf).strip(), once))
    return stmts


def main(argv):
    repeat, maxrows, files = 3, 200, []
    it = iter(argv)
    for a in it:
        if a == "--repeat":
            repeat = int(next(it))
        elif a == "--maxrows":
            maxrows = int(next(it))
        else:
            files.append(a)
    with psycopg.connect(DSN, autocommit=True) as conn:
        for f in files:
            path = Path(f)
            lines = [f"# {path.name}  (repeat={repeat}, median wall time)\n"]
            for i, (sql, once) in enumerate(split_statements(path.read_text()), 1):
                times, rows, desc = [], [], None
                ddl = sql.lstrip().split(None, 1)[0].upper() in ("CREATE", "DROP", "INSERT") or any(
                    l.strip().upper().startswith(("CREATE", "DROP", "INSERT")) for l in sql.splitlines() if not l.strip().startswith("--"))
                for _ in range(1 if (once or ddl) else repeat):
                    t = time.perf_counter()
                    cur = conn.execute(sql)
                    rows = cur.fetchall() if cur.description else []
                    times.append(time.perf_counter() - t)
                    desc = cur.description
                med = statistics.median(times)
                lines.append(f"\n## stmt {i}  median={med:.3f}s  runs={[round(x, 3) for x in times]}\n")
                lines.append("```sql\n" + sql + "\n```\n")
                if desc:
                    lines.append(" | ".join(d.name for d in desc))
                    for r in rows[:maxrows]:
                        lines.append(" | ".join("NULL" if v is None else str(v) for v in r))
                    lines.append(f"-- {len(rows)} rows")
                print(f"{path.name} stmt {i}: {med:.3f}s, {len(rows)} rows", flush=True)
            (OUT / (path.stem + ".out")).write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
