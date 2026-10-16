"""Measure inverted-index build time, VACUUM (REFRESH_TABLE) time and size for variants on bgl."""
import time, psycopg
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
def x(sql):
    t = time.perf_counter(); cur = c.execute(sql); r = cur.fetchall() if cur.description else None
    return (time.perf_counter() - t) * 1000, r
x("CREATE TEXT SEARCH DICTIONARY IF NOT EXISTS r2p_log_f AS split_text(case := 'lower') WITH (frequency)")
x("CREATE TEXT SEARCH DICTIONARY IF NOT EXISTS r2p_log_fp AS split_text(case := 'lower') WITH (frequency, position)")
V = {
 "r2p_v_fpo_incl": "CREATE INDEX r2p_v_fpo_incl ON bgl USING inverted (line_no, message r2p_log, ts, node, component, level, alert_tag) INCLUDE (ts, node, component, level, alert_tag)",
 "r2p_v_fp_incl": "CREATE INDEX r2p_v_fp_incl ON bgl USING inverted (line_no, message r2p_log_fp, ts, node, component, level, alert_tag) INCLUDE (ts, node, component, level, alert_tag)",
 "r2p_v_f_noincl": "CREATE INDEX r2p_v_f_noincl ON bgl USING inverted (line_no, message r2p_log_f, ts, node, component, level, alert_tag)",
 "r2p_v_msg_only": "CREATE INDEX r2p_v_msg_only ON bgl USING inverted (line_no, message r2p_log)",
 "r2p_v_fpo_topk": "CREATE INDEX r2p_v_fpo_topk ON bgl USING inverted (line_no, message r2p_log, ts, node, component, level, alert_tag) INCLUDE (ts, node, component, level, alert_tag) WITH (optimize_top_k = 'bm25(1.2, 0.75)')",
}
for name, ddl in V.items():
    x(f"DROP INDEX IF EXISTS {name}")
    tb, _ = x(ddl)
    tv, _ = x("VACUUM (REFRESH_TABLE) bgl")
    tc, r = x(f"SELECT count(*) FROM {name} WHERE message @@ 'error'")
    _, sz = x(f"SELECT pg_relation_size('{name}')")
    print(f"{name:16s} build={tb/1000:6.1f}s refresh={tv:6.0f}ms first_query={tc:6.0f}ms count={r[0][0]} size={sz[0][0]/1e6:6.1f} MB")
    x(f"DROP INDEX {name}")
