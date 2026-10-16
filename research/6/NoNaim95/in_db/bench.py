#!/usr/bin/env python3
"""bench.py <file.sql> : run ';;'-separated statements; lines starting with '--x3' before a statement mean
'time median of 3'. Prints first rows and wall time. Retries connection if the server restarts."""
import sys, time, statistics, psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"


def connect():
    for i in range(30):
        try:
            return psycopg.connect(DSN, autocommit=True)
        except psycopg.OperationalError:
            time.sleep(5)
    raise SystemExit("server down")


def main():
    text = open(sys.argv[1]).read()
    stmts = [s.strip() for s in text.split(";;") if s.strip()]
    c = connect()
    for s in stmts:
        import re
        m = re.match(r"--x(\d)", s); reps = int(m.group(1)) if m else 1
        label = s.splitlines()[0][:110]
        ts, rows = [], []
        for _ in range(reps):
            for attempt in range(2):
                try:
                    t = time.time(); cur = c.execute(s)
                    rows = cur.fetchall() if cur.description else []
                    ts.append(time.time() - t); break
                except psycopg.OperationalError as e:
                    print("   !! server error, reconnecting:", str(e).splitlines()[0]); c = connect()
                except psycopg.Error as e:
                    print("   !! SQL error:", str(e).splitlines()[0]); ts.append(float('nan')); break
        print(f"[{statistics.median(ts):8.3f}s {f'med{reps} min={min(ts):.3f} ' + str(['%.2f' % x for x in ts]) if reps > 1 else ''}] {label}")
        for r in rows[:int(sys.argv[2]) if len(sys.argv) > 2 else 5]:
            print("      ", " | ".join(str(x)[:120] for x in r))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
