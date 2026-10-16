#!/usr/bin/env python3
"""Experiment 2: apply Loghub-2.0 templates as RE2 regexes inside SereneDB to classify the raw corpus.

Builds r6d_lh2re_<ds>(event_id, template, re, lit_len) from lh2 templates; then classifies DISTINCT raw
messages with (a) a regex JOIN (regexp_full_match with a per-row pattern) on a sample and (b) a generated
CASE chain of constant patterns (most-specific first). Reports time, coverage, ambiguity.
"""
import sys, time, psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"
SPECIAL = set("\\.^$|?*+()[]{}")


def tpl_to_re(t):
    out = []
    for i, part in enumerate(t.split("<*>")):
        if i:
            out.append(".*")
        out.append("".join("\\" + ch if ch in SPECIAL else ch for ch in part))
    return "".join(out)


def lit(s):
    return "'" + s.replace("'", "''") + "'"


def timed(cur, sql):
    t = time.time(); cur.execute(sql)
    r = cur.fetchall() if cur.description else None
    return r, time.time() - t


def main():
    ds = sys.argv[1]  # bgl | openstack
    lh2 = f"lh2_{ds}"
    sample = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    with psycopg.connect(DSN, autocommit=True) as c:
        cur = c.cursor()
        tpls = cur.execute(f"select event_id, any_value(event_template), count(*) from {lh2} group by 1").fetchall()
        rows = [(e, t, tpl_to_re(t), len(t.replace("<*>", "")), n) for e, t, n in tpls]
        rows.sort(key=lambda r: -r[3])  # most literal characters first = most specific first
        cur.execute(f"create or replace table r6d_lh2re_{ds} (event_id varchar, template varchar, re varchar, lit_len int, prio int)")
        cur.executemany(f"insert into r6d_lh2re_{ds} values (%s,%s,%s,%s,%s)",
                        [(e, t, r, l, i) for i, (e, t, r, l, n) in enumerate(rows)])
        print(f"{ds}: {len(rows)} template regexes loaded")
        # distinct raw messages
        _, dt = timed(cur, f"create or replace table r6d_msgs_{ds} as select coalesce(message,'') m, count(*) n from {ds} group by 1")
        nmsg, nlines = cur.execute(f"select count(*), sum(n) from r6d_msgs_{ds}").fetchone()
        print(f"  distinct messages {nmsg} covering {nlines} lines ({dt:.2f}s)")

        # (a) regex join on a sample of distinct messages
        r, dt = timed(cur, f"""select count(distinct s.m), count(*) from (select m from r6d_msgs_{ds} order by hash(m) limit {sample}) s
                               join r6d_lh2re_{ds} t on regexp_full_match(s.m, t.re, 's')""")
        print(f"  (a) regex JOIN sample={sample}: matched msgs={r[0][0]} pairs={r[0][1]} time={dt:.2f}s "
              f"-> {sample*len(rows)/dt/1e6:.2f}M regex evals/s; extrapolated full={dt*nmsg/sample:.0f}s")

        # (b) CASE chain with constant patterns
        case = "case " + " ".join(f"when regexp_full_match(m, {lit(re_)}, 's') then {lit(e)}" for e, t, re_, l, n in rows) + " end"
        times = []
        for _ in range(3):
            _, dt = timed(cur, f"create or replace table r6d_cls_{ds} as select m, n, {case} event_id from r6d_msgs_{ds}")
            times.append(dt)
        times.sort()
        r = cur.execute(f"""select count(*) filter (where event_id is not null), count(*),
                                   sum(n) filter (where event_id is not null), sum(n) from r6d_cls_{ds}""").fetchone()
        print(f"  (b) CASE chain over all distinct msgs: median {times[1]:.2f}s {['%.2f'%x for x in times]}; "
              f"msgs matched {r[0]}/{r[1]}; LINE coverage {r[2]}/{r[3]} = {r[2]/r[3]:.4%}")
        # (b') CASE chain per raw row (no distinct step), one run
        _, dt = timed(cur, f"select count(*) from (select {case.replace('(m,', "(coalesce(message,''),")} e from {ds}) where e is not null")
        print(f"  (b') CASE chain per raw row ({nlines} rows): {dt:.2f}s")
        # ambiguity: messages matching >1 template (from the sample join)
        r = cur.execute(f"""select count(*) from (select s.m from (select m from r6d_msgs_{ds} order by hash(m) limit {sample}) s
                               join r6d_lh2re_{ds} t on regexp_full_match(s.m, t.re, 's') group by 1 having count(*)>1)""").fetchone()
        print(f"  ambiguous msgs in sample (match >1 template): {r[0]}")
        for row in cur.execute(f"select m, n from r6d_cls_{ds} where event_id is null order by n desc limit 8"):
            print("    unmatched:", row[1], row[0][:150])


if __name__ == "__main__":
    main()
