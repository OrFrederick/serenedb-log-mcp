#!/usr/bin/env python3
"""Regex-masking template keys inside SereneDB + approximate Grouping Accuracy vs Loghub-2.0.

Usage: python mask_ga.py <version> [--time]
Each version is an ordered list of (RE2 pattern, replacement) applied with regexp_replace(...,'g').
The chain is installed as a SereneDB macro r6d_mask_<version>(s).

GA (Zhu et al. 2019 / Jiang et al. 2024 definition): a line is correctly grouped iff the set of
lines sharing its predicted group equals the set of lines sharing its ground-truth EventId.
Equivalently: line in pair (key, eid) is correct iff key maps to exactly one eid AND eid maps to
exactly one key. Computed in SQL over lh2_* tables.
"""
import sys, time, statistics, psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"

# ---- rule sets --------------------------------------------------------------------------
V1 = [  # naive: hex, then any digit run
    (r"0x[0-9a-fA-F]+", "<*>"),
    (r"[0-9]+", "<*>"),
]
V2 = [  # token-ish: IPs, UUIDs, hex words, numbers, then collapse
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<*>"),
    (r"\d+\.\d+\.\d+\.\d+(:\d+)?", "<*>"),
    (r"0x[0-9a-fA-F]+", "<*>"),
    (r"\b[0-9a-fA-F]{6,}\b", "<*>"),          # bare hex words (iar 00106348 dear 0303188c)
    (r"\b[0-9]+\b", "<*>"),
]
V3 = V2[:-1] + [
    (r"R\d\d-M\d(-[A-Z]\w*(-[A-Z]\w*)?(:J\d\d-U\d\d)?)?", "<*>"),  # BGL node / location ids
    (r"(/[\w.\-<>*]+)+/?", "<*>"),               # unix paths
    (r"[0-9]+", "<*>"),                          # any digit run, also inside words (core.123)
    (r"<\*>([^\s]*<\*>)+", "<*>"),               # collapse adjacent wildcards inside a token
]
V4 = [
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<*>"),
    (r"\d+\.\d+\.\d+\.\d+(:\d+)?", "<*>"),
    (r"R\d\d-M\d(-[A-Z]\w*(-[A-Z]\w*)?(:J\d\d-U\d\d)?)?", "<*>"),
    (r"(/[\w.\-]+)+/?", "<*>"),
    (r"0x[0-9a-fA-F]+", "<*>"),
    (r"\b[0-9a-fA-F]*[0-9][0-9a-fA-F]*\b", "<*>"),  # any hex-ish token containing a digit
    (r"[0-9]+", "<*>"),
    (r"<\*>([^\s]*<\*>)+", "<*>"),
]
# v5: generic, dataset-agnostic: mask ids, then any whitespace token that contains a digit
V5 = [
    (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<*>"),  # UUID
    (r"\d+\.\d+\.\d+\.\d+(:\d+)?", "<*>"),                                                   # IPv4[:port]
    (r"\b([0-9A-Fa-f]{2}:){2,}[0-9A-Fa-f]{2}\b", "<*>"),                                       # MAC / colon-hex
    (r"\b[\w-]+(\.[\w-]+)+\.(com|net|org|edu|us|local|io)\b", "<*>"),                          # hostnames
    (r"(/[^\s/:,()\[\]\"']+)+/?", "<*>"),                                                    # unix paths
    (r"[^\s(),=:\[\]\"']*\d[^\s(),=:\[\]\"']*", "<*>"),                                       # any token with a digit
]
# v6: v5 + collapse runs of wildcards separated by spaces
V6 = V5 + [(r"<\*>( <\*>)+", "<*>")]
# v7: v6 + a few BGL/OpenStack keyword-argument rules (fitted on lh2 errors; see held-out split)
V7 = V5 + [
    (r"(for program |Error loading |node map file |from file )[^\s:]+( VN)?", r"\1<*>"),     # program / file args
    (r"chdir\([^)]*\)", "chdir(<*>)"),
    (r"\b[xyzXYZ][+-](\s|$)", r"<*>\1"),                                                      # torus directions
    (r"\b[XYZT] coordinate", "<*> coordinate"),
    (r"L<\*> (EDRAM|directory)", "L<*> <*>"),
    (r"(status=|ASSERT condition: |for reason |wanted: |machine state register: ).*", r"\1<*>"), # rest-of-line vars
    (r"<\*>( <\*>)+", "<*>"),
]
# v8 generic: like v5 but digit-token excludes '.', then collapse <*>[.,:]<*> and <*> <*>
G8 = V5[:-1] + [
    (r"[^\s(),=:\[\].\"']*\d[^\s(),=:\[\].\"']*", "<*>"),   # token (no dots) containing a digit
    (r"<\*>([.,:]<\*>)+", "<*>"),                               # DV/CV-like collapse
    (r"<\*>( <\*>)+", "<*>"),                                   # consecutive space-separated vars
]
V8 = G8
# v9 = v8 + dataset keyword rules (BGL/OpenStack), fitted on lh2 error lists of v4..v8
T9 = [
    (r"(for program |Error loading |node map file |from file )[^:]+:", r"\1<*>:"),   # program / file args up to ':'
    (r"chdir\([^)]*\)", "chdir(<*>)"),
    (r"\b[xyzXYZ][+-](\s|$)", r"<*>\1"),                    # torus/tree link directions
    (r"\b[XYZT] coordinate", "<*> coordinate"),
    (r"\b(EDRAM|directory) error", "<*> error"),
    (r"(status=|ASSERT condition: |for reason |wanted: |machine state register: |has been started: |monitor caught |Message=).*", r"\1<*>"),
    (r"(by|cable|Port|the) \(?[A-Za-z]+\)?( |$)", r"\1 <*>\2"),   # NOT used (too broad) - placeholder
]
T9 = T9[:-1] + [(r"<\*>( <\*>)+", "<*>")]
V9 = G8 + T9
# v10 generic = v8 + Loghub-2.0 BL/US rule (true/false/null/root/admin) + leading-dot/adjacent-var collapse
G10 = G8[:-1] + [
    (r"(?i)\b(true|false|null|root|admin)\b", "<*>"),
    (r"\.*(<\*>)+", "<*>"),
    (r"<\*>([.,:]<\*>)+", "<*>"),
    (r"<\*>( <\*>)+", "<*>"),
]
T10 = [
    (r"(for program |Error loading |from file )[^:]+:", r"\1<*>:"),
    (r"(node map file )[^\s:]+", r"\1<*>"),
    (r"chdir\([^)]*\)", "chdir(<*>)"),
    (r"\b[xyzXYZ][+-](\s|$)", r"<*>\1"),
    (r"\b[XYZT] coordinate", "<*> coordinate"),
    (r"\b(EDRAM|directory) error", "<*> error"),
    (r"(status=|ASSERT condition: |for reason |wanted: |machine state register: |monitor caught |Message=).*", r"\1<*>"),
    (r"<\*>( <\*>)+", "<*>"),
]
V10 = G10 + T10
VERSIONS = {"v1": V1, "v2": V2, "v3": V3, "v4": V4, "v5": V5, "v6": V6, "v7": V7, "v8": V8, "v9": V9, "v10": V10, "g10": G10}


def sql_lit(s):
    return "'" + s.replace("'", "''") + "'"


def chain(rules, arg="s"):
    e = arg
    for pat, rep in rules:
        e = f"regexp_replace({e}, {sql_lit(pat)}, {sql_lit(rep)}, 'g')"
    return e


GA_SQL = """
with d as (select {col} c, event_id, count(*) n from {tbl} {where} group by 1,2),
k as (select {mac}(c) key, event_id, sum(n) n from d group by 1,2),
kc as (select key, count(*) ne from k group by 1),
ec as (select event_id, count(*) nk from k group by 1)
select sum(case when ne=1 and nk=1 then n else 0 end)::double/sum(n) ga,
       count(distinct key) keys, sum(n) lines,
       sum(case when ne=1 and nk=1 then 1 else 0 end)::double/count(distinct key) pga,
       sum(case when ne=1 and nk=1 then 1 else 0 end)::double/count(distinct event_id) rga
from k join kc using(key) join ec using(event_id)
"""


def run(cur, sql):
    t = time.time(); cur.execute(sql)
    r = cur.fetchall() if cur.description else None
    return r, time.time() - t


def main():
    ver = sys.argv[1]
    rules = VERSIONS[ver]
    mac = f"r6d_mask_{ver}"
    with psycopg.connect(DSN, autocommit=True) as c:
        cur = c.cursor()
        cur.execute(f"create or replace macro {mac}(s) as {chain(rules)}")
        print(f"macro {mac}: {len(rules)} regexp_replace steps")
        for tbl, half in (("lh2_bgl", 2315630), ("lh2_openstack", 103816)):
            for label, where in (("all", ""), ("1st-half", f"where line_id<={half}"), ("2nd-half", f"where line_id>{half}")):
                r, dt = run(cur, GA_SQL.format(col="content", tbl=tbl, mac=mac, where=where))
                ga, keys, lines, p, rr = r[0]
                fga = 2*p*rr/(p+rr) if p+rr else 0
                print(f"  {tbl:13s} {label:8s}: GA={ga:.4f} FGA={fga:.4f} keys={keys} lines={lines} ({dt:.2f}s)")
        if "--time" in sys.argv:
            for tbl in ("bgl", "openstack"):
                ts = []
                for _ in range(3):
                    r, dt = run(cur, f"select count(*), count(distinct {mac}(message)) from {tbl}")
                    ts.append(dt)
                ts2 = []
                for _ in range(3):
                    r2, dt = run(cur, f"select count(distinct {mac}(m)) from (select distinct message m from {tbl})")
                    ts2.append(dt)
                print(f"  {tbl}: rows={r[0][0]} distinct_keys={r[0][1]} full-scan median={statistics.median(ts):.2f}s {['%.2f'%x for x in ts]}"
                      f" | distinct-first median={statistics.median(ts2):.2f}s")


if __name__ == "__main__":
    main()
