"""Paired bootstrap 95% CI for MRR differences (main set n=57, holdout n=16)."""
import sys, os, json, numpy as np, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import metrics, HERE
E = json.load(open(f"{HERE}/results/r7e_eval.json")); H = json.load(open(f"{HERE}/results/r7e_holdout.json"))
L = yaml.safe_load(open(f"{HERE}/r7e_labels.yaml")); rel = {q["id"]: q["relevant"] for k, v in L.items() if k != "bias" for q in v}
rng = np.random.default_rng(0)
def ci(runs, a, b):
    q = sorted(runs[a]); d = np.array([metrics(runs[a][x], rel[x])["MRR"] - metrics(runs[b][x], rel[x])["MRR"] for x in q])
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(10000)]
    return round(d.mean(), 3), [round(float(np.percentile(bs, 2.5)), 3), round(float(np.percentile(bs, 97.5)), 3)], len(d)
pairs = [("vec:all-MiniLM-L6-v2", "bm25"), ("vec:bge-small-en-v1.5", "bm25"), ("vec:granite-embedding-small-english-r2", "bm25"),
         ("vec:all-MiniLM-L6-v2", "bm25+syn"), ("rrf(bm25+syn,all-MiniLM-L6-v2)", "bm25+syn"), ("rrf(bm25+syn,all-MiniLM-L6-v2)", "vec:all-MiniLM-L6-v2"),
         ("rrf(bm25,all-MiniLM-L6-v2)", "vec:all-MiniLM-L6-v2"), ("bm25+syn", "bm25")]
out = {}
for a, b in pairs:
    out[f"main: {a} - {b}"] = ci(E["runs"], a, b)
    if a in H["runs"] and b in H["runs"]: out[f"holdout: {a} - {b}"] = ci(H["runs"], a, b)
for k, v in out.items(): print(f"{k:75s} diff={v[0]:+.3f} CI95={v[1]} n={v[2]}")
json.dump(out, open(f"{HERE}/results/r7e_bootstrap.json", "w"), indent=1)
