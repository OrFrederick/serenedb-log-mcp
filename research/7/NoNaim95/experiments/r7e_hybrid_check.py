"""Check that the single-statement SQL RRF (docs cookbook pattern) reproduces the Python RRF used in r7e_eval."""
import sys, os, json, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, metrics, HERE
R, L = load(); E = json.load(open(f"{HERE}/results/r7e_eval.json"))
Z = np.load(f"{HERE}/emb/BAAI__bge-small-en-v1.5.npz"); qids = list(Z["qids"]); Q = Z["Q"]
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
vec = lambda v: "[" + ",".join(f"{x:.6f}" for x in v) + "]::FLOAT[384]"
qm = {q["id"]: (ds, q) for ds in ("BGL", "OpenStack") for q in L[ds]}
same = 0; ms = []
for j, qid in enumerate(qids):
    ds, q = qm[qid]; t = q["text"].replace("'", "''")
    sql = f"""WITH fused AS (
  SELECT event_id, ROW_NUMBER() OVER (ORDER BY s DESC, id) AS rank FROM (
    SELECT event_id, id, BM25(r7e_tpl_idx.tableoid) AS s FROM r7e_tpl_idx WHERE body @@ '{t}' AND dataset = '{ds}' ORDER BY s DESC, id LIMIT 20) lex
  UNION ALL
  SELECT event_id, ROW_NUMBER() OVER (ORDER BY dist, id) AS rank FROM (
    SELECT event_id, id, e_bge <=> {vec(Q[j])} AS dist FROM r7e_tpl_idx WHERE dataset = '{ds}' ORDER BY dist, id LIMIT 20) v)
SELECT event_id FROM fused GROUP BY event_id ORDER BY SUM(1.0 / (60 + rank)) DESC, event_id LIMIT 10"""
    got = [r[0] for r in c.execute(sql).fetchall()]
    py = E["runs"]["rrf(bm25,bge-small-en-v1.5)"][qid][:10]
    same += set(got) == set(py); ms.append(metrics(got, q["relevant"])["MRR"])
print(f"SQL RRF top-10 set == Python RRF top-10 set: {same}/{len(qids)}; SQL-RRF mean MRR {np.mean(ms):.3f} vs python {E['summary']['rrf(bm25,bge-small-en-v1.5)']['ALL']['MRR']}")
