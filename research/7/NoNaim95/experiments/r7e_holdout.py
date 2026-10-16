"""Evaluate frozen methods on the holdout queries (no tuning)."""
import sys, os, json, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, metrics, PREFIX, doc_text, HERE
from r7e_synonyms import expand
from fastembed import TextEmbedding
R, L = load()
conn = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
esc = lambda s: s.replace("'", "''")
def bm25(ds, t): return [r[0] for r in conn.execute(f"SELECT event_id, BM25(r7e_tpl_idx.tableoid) AS s FROM r7e_tpl_idx WHERE body @@ '{esc(t)}' AND dataset = '{ds}' ORDER BY s DESC, id LIMIT 20").fetchall()]
def rrf(*ls, k=60):
    sc = {}
    for l in ls:
        for i, e in enumerate(l): sc[e] = sc.get(e, 0) + 1 / (k + i + 1)
    return [e for e, _ in sorted(sc.items(), key=lambda x: -x[1])]
qs = [(ds, q) for key, ds in (("holdout_BGL", "BGL"), ("holdout_OpenStack", "OpenStack")) for q in L[key]]
runs = {"bm25": {}, "bm25+syn": {}}
for ds, q in qs:
    runs["bm25"][q["id"]] = bm25(ds, q["text"]); runs["bm25+syn"][q["id"]] = bm25(ds, expand(q["text"]))
for model, cache in (("BAAI/bge-small-en-v1.5", "/tmp/r7e_fe/bge-small"), ("sentence-transformers/all-MiniLM-L6-v2", "/tmp/r7e_fe/sentence-transformers_all-MiniLM-L6-v2"),
                     ("ibm-granite/granite-embedding-small-english-r2", "/tmp/r7e_fe/ibm-granite_granite-embedding-small-english-r2")):
    name = model.split("/")[-1]; D = np.load(f"{HERE}/emb/{model.replace('/', '__')}.npz")["D_clean"]; D = D / np.linalg.norm(D, axis=1, keepdims=True)
    m = TextEmbedding(model, cache_dir=cache); qp, _ = PREFIX.get(model, ("", ""))
    for ds, q in qs:
        rows = [i for i, r in enumerate(R) if r["dataset"] == ds]
        v = np.array(list(m.embed([qp + q["text"]])))[0]; v /= np.linalg.norm(v)
        runs.setdefault(f"vec:{name}", {})[q["id"]] = [R[rows[o]]["event_id"] for o in np.argsort(-(D[rows] @ v))[:20]]
    runs[f"rrf(bm25,{name})"] = {qid: rrf(runs["bm25"][qid], runs[f"vec:{name}"][qid]) for qid in runs["bm25"]}
    runs[f"rrf(bm25+syn,{name})"] = {qid: rrf(runs["bm25+syn"][qid], runs[f"vec:{name}"][qid]) for qid in runs["bm25"]}
rel = {q["id"]: q["relevant"] for _, q in qs}
summ = {}
for meth, rq in runs.items():
    ms = [metrics(rq[qid], rel[qid]) for qid in rq]
    summ[meth] = {k: round(float(np.mean([x[k] for x in ms])), 3) for k in ("cR@5", "cR@10", "P@5", "MRR")} | {"zero": sum(len(rq[qid]) == 0 for qid in rq)}
for meth in sorted(summ, key=lambda m: -summ[m]["MRR"]): print(f"{meth:50s}", summ[meth])
json.dump(dict(summary=summ, runs=runs), open(f"{HERE}/results/r7e_holdout.json", "w"), indent=0)
