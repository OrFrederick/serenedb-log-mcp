"""Exact vs IVF vector scan in SereneDB at 368 vectors, numpy baseline, and hybrid RRF in one SQL statement."""
import sys, os, time, json, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, metrics, HERE
R, L = load()
Z = np.load(f"{HERE}/emb/BAAI__bge-small-en-v1.5.npz"); qids = list(Z["qids"]); Q = Z["Q"]
D = Z["D_clean"]; Dn = D / np.linalg.norm(D, axis=1, keepdims=True)
qm = {q["id"]: (ds, q) for ds in ("BGL", "OpenStack") for q in L[ds]}
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
vec = lambda v: "[" + ",".join(f"{x:.6f}" for x in v) + "]::FLOAT[384]"
def med(f, n=5):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); r = f(); ts.append((time.perf_counter() - t0) * 1000)
    return float(np.median(ts)), r
out = {"exact_ms": [], "ivf_ms": [], "numpy_ms": [], "hyb_sql_ms": []}; agree = 0; agree_np = 0; hyb_agree = 0
plan_ivf = c.execute(f"EXPLAIN SELECT event_id FROM r7e_tpl_idx ORDER BY e_bge <=> {vec(Q[0])} LIMIT 10").fetchall()
plan_ex = c.execute(f"EXPLAIN SELECT event_id FROM r7e_tpl ORDER BY e_bge <=> {vec(Q[0])} LIMIT 10").fetchall()
for j, qid in enumerate(qids):
    ds, q = qm[qid]; v = vec(Q[j])
    t_ex, ex = med(lambda: [r[0] for r in c.execute(f"SELECT event_id FROM r7e_tpl WHERE dataset='{ds}' ORDER BY e_bge <=> {v}, id LIMIT 10").fetchall()])
    t_iv, iv = med(lambda: [r[0] for r in c.execute(f"SELECT event_id FROM r7e_tpl_idx WHERE dataset='{ds}' ORDER BY e_bge <=> {v}, id LIMIT 10").fetchall()])
    rows = [i for i, r in enumerate(R) if r["dataset"] == ds]
    qn = Q[j] / np.linalg.norm(Q[j])
    t_np, npr = med(lambda: [R[rows[o]]["event_id"] for o in np.argsort(-(Dn[rows] @ qn))[:10]])
    agree += ex == iv; agree_np += ex == npr
    out["exact_ms"].append(t_ex); out["ivf_ms"].append(t_iv); out["numpy_ms"].append(t_np)
    t = q["text"].replace("'", "''")
    hsql = f"""WITH fused AS (
  SELECT event_id, RANK() OVER (ORDER BY s DESC, id) AS rank FROM (
    SELECT event_id, id, BM25(r7e_tpl_idx.tableoid) AS s FROM r7e_tpl_idx
    WHERE body @@ '{t}' AND dataset = '{ds}' ORDER BY s DESC, id LIMIT 20) lex
  UNION ALL
  SELECT event_id, RANK() OVER (ORDER BY dist, id) AS rank FROM (
    SELECT event_id, id, e_bge <=> {v} AS dist FROM r7e_tpl_idx
    WHERE dataset = '{ds}' ORDER BY dist, id LIMIT 20) vec)
SELECT event_id, SUM(1.0 / (60 + rank)) AS score FROM fused GROUP BY event_id ORDER BY score DESC, event_id LIMIT 10"""
    t_h, h = med(lambda: [r[0] for r in c.execute(hsql).fetchall()])
    out["hyb_sql_ms"].append(t_h)
    if j == 0: out["hybrid_sql_example"] = hsql
summ = {k: dict(median=round(float(np.median(v)), 2), p95=round(float(np.percentile(v, 95)), 2)) for k, v in out.items() if k.endswith("_ms")}
summ["exact_eq_ivf_top10"] = f"{agree}/{len(qids)}"; summ["exact_eq_numpy_top10"] = f"{agree_np}/{len(qids)}"
summ["plan_ivf_top"] = "\n".join(r[0] for r in plan_ivf)[:1500]; summ["plan_exact_top"] = "\n".join(r[0] for r in plan_ex)[:1500]
summ["hybrid_sql_example"] = out["hybrid_sql_example"][:600] + " ..."
print(json.dumps(summ, indent=1)); json.dump(summ, open(f"{HERE}/results/r7e_vec_sql.json", "w"), indent=1)
