"""SereneDB ai_embed via local Ollama (all-minilm): latency per call, bulk embed of 368 templates
into FLOAT[384] + IVF index, query-time ai_embed inside ORDER BY <=>, and agreement with fastembed MiniLM."""
import sys, os, time, json, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, metrics, HERE
R, L = load()
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
c.execute("SET sdb_ai_allow_insecure_endpoint = true")
out = {}
def timed(sql, n=7):
    ts = []
    for i in range(n):
        t0 = time.time(); rows = c.execute(sql).fetchall(); ts.append((time.time() - t0) * 1000)
    return float(np.median(ts)), rows
# 1. single-call latency (distinct texts to avoid any provider caching)
ts = []
for i in range(15):
    t0 = time.time(); c.execute(f"SELECT ai_embed('memory problems variant {i}', 'all-minilm', 'r7e_ollama')").fetchall(); ts.append((time.time() - t0) * 1000)
out["single_call_ms_median"] = float(np.median(ts)); out["single_call_ms_first"] = ts[0]
# 2. bulk embed the templates (copy of r7e_tpl, minus vectors)
c.execute("DROP TABLE IF EXISTS r7e_tpl_ol")
c.execute("CREATE TABLE r7e_tpl_ol (id INTEGER, dataset VARCHAR, event_id VARCHAR, body VARCHAR, e FLOAT[384])")
t0 = time.time()
c.execute("INSERT INTO r7e_tpl_ol SELECT id, dataset, event_id, body, ai_embed(body, 'all-minilm', 'r7e_ollama')::FLOAT[384] FROM r7e_tpl")
out["bulk_embed_368_s"] = time.time() - t0
t0 = time.time()
c.execute("CREATE INDEX r7e_tpl_ol_idx ON r7e_tpl_ol USING inverted (id, dataset, event_id, body r7e_en, e ivf(metric = 'cosine'))")
c.execute("VACUUM (REFRESH_TABLE) r7e_tpl_ol"); out["index_s"] = time.time() - t0
# 3. query-time: embed + rank in one statement, for each labelled query
res = {}; lat = []
for ds in ("BGL", "OpenStack"):
    for q in L[ds]:
        t = q["text"].replace("'", "''")
        sql = (f"SELECT event_id FROM r7e_tpl_ol_idx WHERE dataset = '{ds}' ORDER BY e <=> ai_embed('{t}', 'all-minilm', 'r7e_ollama')::FLOAT[384] LIMIT 20")
        t0 = time.time(); ranked = [r[0] for r in c.execute(sql).fetchall()]; lat.append((time.time() - t0) * 1000)
        # exact scan on base table for comparison
        sql2 = sql.replace("r7e_tpl_ol_idx", "r7e_tpl_ol")
        ranked2 = [r[0] for r in c.execute(sql2).fetchall()]
        res[q["id"]] = dict(ivf=metrics(ranked, q["relevant"]), exact=metrics(ranked2, q["relevant"]), same_top10=ranked[:10] == ranked2[:10])
out["query_embed_rank_ms_median"] = float(np.median(lat)); out["query_embed_rank_ms_p95"] = float(np.percentile(lat, 95))
for k in ("ivf", "exact"):
    out[f"{k}_mean"] = {m: round(float(np.mean([v[k][m] for v in res.values()])), 3) for m in ("cR@5", "cR@10", "P@5", "MRR")}
out["ivf_equals_exact_top10"] = sum(v["same_top10"] for v in res.values())
# 4. agreement with fastembed all-MiniLM-L6-v2 vectors
ol = {r[0]: np.array(r[1], dtype=float) for r in c.execute("SELECT id, e FROM r7e_tpl_ol").fetchall()}
fe = np.load(f"{HERE}/emb/sentence-transformers__all-MiniLM-L6-v2.npz")["D_clean"]
cos = [float(ol[i] @ fe[i] / np.linalg.norm(ol[i]) / np.linalg.norm(fe[i])) for i in range(len(R))]
out["cos_ollama_vs_fastembed_minilm"] = dict(min=min(cos), median=float(np.median(cos)))
print(json.dumps(out, indent=1)); json.dump(out | {"per_query": res}, open(f"{HERE}/results/r7e_ai_embed.json", "w"), indent=0)
