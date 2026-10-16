"""IVF / HNSW index vs exact scan in SereneDB at 368 template vectors (granite-small-r2, 384 dims)."""
import json, time, numpy as np, psycopg, statistics as st
CACHE = "/private/tmp/claude-501/-Users-frederick-Development-LogSearcher/bbdd15e1-b2d7-42b2-8c5d-c8e1bbcc9ff9/scratchpad/emb"
M = "granite-small-r2"; z = np.load(f"{CACHE}/{M}.npz"); T = json.load(open("templates.json"))
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
lit = lambda v: "[" + ",".join(f"{x:.6f}" for x in v) + "]"
c.execute("DROP TABLE IF EXISTS r7_vec")
c.execute("CREATE TABLE r7_vec (dataset VARCHAR, template_id VARCHAR, emb FLOAT[384])")
rows = [(ds, t["id"], lit(z[f"{ds}_docs"][i])) for ds in ("bgl", "openstack") for i, t in enumerate(T[ds])]
with c.cursor() as cur: cur.executemany("INSERT INTO r7_vec VALUES (%s, %s, %s::FLOAT[384])", rows)
res = {}
for name, ddl in [("ivf", "CREATE INDEX r7_vec_ivf ON r7_vec USING inverted (dataset, template_id, emb ivf (metric = 'cosine'))"),
                  ("hnsw", "CREATE INDEX r7_vec_hnsw ON r7_vec USING inverted (dataset, template_id, emb hnsw (metric = 'cosine'))")]:
    t0 = time.perf_counter(); c.execute(ddl); c.execute("VACUUM (REFRESH_TABLE) r7_vec"); res[f"{name}_build_ms"] = round((time.perf_counter() - t0) * 1000, 1)
qs = [(ds, lit(q)) for ds in ("bgl", "openstack") for q in z[f"{ds}_q"]]
def bench(src, sql_tpl):
    lat, out = [], []
    for ds, q in qs:
        sql = sql_tpl.format(src=src, q=q)
        c.execute(sql, (ds,)).fetchall()  # warm
        t0 = time.perf_counter(); r = c.execute(sql, (ds,)).fetchall(); lat.append(time.perf_counter() - t0)
        out.append([x[0] for x in r])
    return round(st.median(lat) * 1000, 2), out
tpl_filter = "SELECT template_id FROM {src} WHERE dataset = %s ORDER BY emb <=> '{q}'::FLOAT[384] LIMIT 10"
res["exact_ms"], ex = bench("r7_vec", tpl_filter)
res["ivf_ms"], iv = bench("r7_vec_ivf", tpl_filter)
res["hnsw_ms"], hn = bench("r7_vec_hnsw", tpl_filter)
res["ivf_recall10_vs_exact"] = round(np.mean([len(set(a) & set(b)) / 10 for a, b in zip(ex, iv)]), 3)
res["hnsw_recall10_vs_exact"] = round(np.mean([len(set(a) & set(b)) / 10 for a, b in zip(ex, hn)]), 3)
# check SereneDB exact == numpy exact
D = {ds: z[f"{ds}_docs"] / np.linalg.norm(z[f"{ds}_docs"], axis=1, keepdims=True) for ds in ("bgl", "openstack")}
npx = []
for ds in ("bgl", "openstack"):
    Q = z[f"{ds}_q"] / np.linalg.norm(z[f"{ds}_q"], axis=1, keepdims=True)
    for q in Q: npx.append([T[ds][j]["id"] for j in np.argsort(-(D[ds] @ q))[:10]])
res["sql_exact_vs_numpy_overlap10"] = round(np.mean([len(set(a) & set(b)) / 10 for a, b in zip(ex, npx)]), 3)
plan = c.execute("EXPLAIN " + tpl_filter.format(src="r7_vec_ivf", q=qs[0][1]), ("bgl",)).fetchall()
res["ivf_plan"] = [p[0] for p in plan][:12]  # post-processed to strip box-drawing chars
plan = c.execute("EXPLAIN " + tpl_filter.format(src="r7_vec", q=qs[0][1]), ("bgl",)).fetchall()
res["exact_plan"] = [p[0] for p in plan][:12]
json.dump(res, open("ivf_vs_exact.json", "w"), indent=1); print(json.dumps(res, indent=1))
