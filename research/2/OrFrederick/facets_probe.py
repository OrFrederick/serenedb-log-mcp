import psycopg, time, statistics
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
def t(name, sql, p=()):
    ts=[]
    for i in range(4):
        s=time.perf_counter(); r=c.execute(sql,p).fetchall(); ts.append((time.perf_counter()-s)*1000)
    print(f"{name}: warm median {statistics.median(ts[1:]):.0f} ms -> {r[:6]}")
t("facets_grouping_sets", """
SELECT component, level, count(*) AS n, count(DISTINCT node) AS nodes FROM r2_bgl_fts
WHERE content @@ websearch_to_tsquery(%s) GROUP BY GROUPING SETS ((component), (level), ()) ORDER BY n DESC""", ('parity',))
t("facets_single_pass_summary", """
SELECT count(*) AS total, count(DISTINCT node) AS nodes, count(DISTINCT rack) AS racks, min(ts), max(ts),
       mode(component) AS top_component FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery(%s)""", ('parity',))
t("baseline_ilike_scan_count", "select count(*) from bgl where content ilike %s", ('%parity%',))
t("index_count", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s)", ('parity',))
t("baseline_ilike_phrase", "select count(*) from bgl where content ilike %s", ('%failed to read message prefix%',))
t("index_phrase", "select count(*) from r2_bgl_fts where content @@ websearch_to_tsquery(%s)", ('"failed to read message prefix"',))
