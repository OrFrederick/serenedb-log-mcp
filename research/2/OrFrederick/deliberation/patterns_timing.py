"""Deliberation #2: re-time top_patterns regex fallback (ours: 132 ms, theirs: 11.1 s) and
check get_context same_instance claim. Shared instance, read-only, client-side cancel at 60 s."""
import psycopg, time, threading, statistics
DSN="host=localhost port=7890 dbname=postgres user=postgres password=postgres"
c=psycopg.connect(DSN, autocommit=True)
def run(name, sql, params=None, reps=3):
    ts=[]; rows=None
    for _ in range(reps):
        t=threading.Timer(60, c.cancel_safe); t.start()
        t0=time.perf_counter()
        try: rows=c.execute(sql, params).fetchall()
        except Exception as e: rows=[("ERR", str(e)[:120])]
        finally: t.cancel()
        ts.append((time.perf_counter()-t0)*1000)
    print(f"{name}: median {statistics.median(ts):.0f} ms runs={[round(x) for x in ts]} first={rows[:2]}", flush=True)
    return rows
MASK="regexp_replace(content, '(0x[0-9a-fA-F]+|[0-9a-f]{8,}|[0-9]+(\\.[0-9]+)*|/[^ ]*)', '<*>', 'g')"
run("rows_in_our_scope", "SELECT count(*) FROM bgl WHERE list_contains(%s::text[], level) AND ts >= '2005-06-01' AND ts < '2006-02-01'", [['FATAL','FAILURE','ERROR','SEVERE']], reps=1)
run("top_patterns_our_scope(levels FATAL/FAILURE/ERROR/SEVERE)", f"SELECT {MASK} p, count(*) n FROM bgl WHERE list_contains(%s::text[], level) AND ts >= '2005-06-01' AND ts < '2006-02-01' GROUP BY 1 ORDER BY n DESC LIMIT 10", [['FATAL','FAILURE','ERROR','SEVERE']])
run("top_patterns_whole_bgl", f"SELECT {MASK} p, count(*) n FROM bgl GROUP BY 1 ORDER BY n DESC LIMIT 20")
run("distinct_masked_whole_bgl", f"SELECT count(DISTINCT {MASK}) FROM bgl", reps=1)
# get_context same_instance: lines mentioning an instance UUID in text but with a different/NULL instance_id
run("os_uuid_in_text_not_in_instance_id", r"""SELECT count(*) FILTER (WHERE instance_id IS NULL OR instance_id='' ) n_null_iid, count(*) n
 FROM openstack WHERE regexp_matches(content, '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
 AND regexp_extract(content, '/servers/([0-9a-f-]{36})', 1) <> ''""", reps=1)
