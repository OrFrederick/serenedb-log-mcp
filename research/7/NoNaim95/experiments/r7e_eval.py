"""Evaluate retrieval methods on the labelled query set. Writes results/r7e_eval.json + prints tables."""
import sys, os, json, glob, time, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, metrics, HERE
from r7e_synonyms import expand
R, L = load()
idx_of = {i: r for i, r in enumerate(R)}
ds_rows = {ds: [i for i, r in enumerate(R) if r["dataset"] == ds] for ds in ("BGL", "OpenStack")}
K = 20
conn = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
def esc(s): return s.replace("'", "''")
def bm25(ds, text):
    sql = (f"SELECT event_id, BM25(r7e_tpl_idx.tableoid) AS s FROM r7e_tpl_idx WHERE body @@ '{esc(text)}' "
           f"AND dataset = '{ds}' ORDER BY s DESC, id LIMIT {K}")
    return [r[0] for r in conn.execute(sql).fetchall()]
def rrf(*lists, k=60):
    sc = {}
    for l in lists:
        for rank, e in enumerate(l): sc[e] = sc.get(e, 0) + 1 / (k + rank + 1)
    return [e for e, _ in sorted(sc.items(), key=lambda x: -x[1])]
def lex(t): return set(conn.execute(f"SELECT ts_lexize('r7e_en', '{esc(t)}')").fetchone()[0] or [])
from r7e_common import doc_text
overlap = {}
for ds in ("BGL", "OpenStack"):
    for q in L[ds]:
        rel_tok = set().union(*[lex(doc_text(r)) for r in R if r["dataset"] == ds and r["event_id"] in q["relevant"]])
        overlap[q["id"]] = sorted(lex(q["text"]) & rel_tok)
runs = {}  # method -> {qid: ranked list}
qmeta = {}
for ds in ("BGL", "OpenStack"):
    for q in L[ds]:
        qmeta[q["id"]] = (ds, q)
        runs.setdefault("bm25", {})[q["id"]] = bm25(ds, q["text"])
        runs.setdefault("bm25+syn", {})[q["id"]] = bm25(ds, expand(q["text"]))
for f in sorted(glob.glob(f"{HERE}/emb/*.npz")):
    name = os.path.basename(f)[:-4].split("__")[-1]
    Z = np.load(f); qids = list(Z["qids"]); Q = Z["Q"] / np.linalg.norm(Z["Q"], axis=1, keepdims=True)
    for variant in ("raw", "clean", "meta"):
        D = Z[f"D_{variant}"]; D = D / np.linalg.norm(D, axis=1, keepdims=True)
        m = f"vec:{name}" + ("" if variant == "clean" else f"[{variant}]")
        for j, qid in enumerate(qids):
            ds = qmeta[qid][0]; rows = ds_rows[ds]
            s = D[rows] @ Q[j]; order = np.argsort(-s)[:K]
            runs.setdefault(m, {})[qid] = [R[rows[o]]["event_id"] for o in order]
vec_methods = [m for m in runs if m.startswith("vec:") and "[" not in m]
for m in vec_methods:
    runs[f"rrf(bm25,{m[4:]})"] = {q: rrf(runs["bm25"][q], runs[m][q]) for q in qmeta}
    runs[f"rrf(bm25+syn,{m[4:]})"] = {q: rrf(runs["bm25+syn"][q], runs[m][q]) for q in qmeta}
# score
res = {}
for m, rq in runs.items():
    for qid, ranked in rq.items():
        ds, q = qmeta[qid]
        res.setdefault(m, {})[qid] = metrics(ranked, q["relevant"]) | {"n_ret": len(ranked)}
def agg(m, filt):
    vals = [v for qid, v in res[m].items() if filt(qmeta[qid])]
    return {k: round(float(np.mean([v[k] for v in vals])), 3) for k in vals[0]} | {"n": len(vals), "zero": sum(v["n_ret"] == 0 for v in vals)}
summary = {}
groups = {"ALL": lambda x: True, "BGL": lambda x: x[0] == "BGL", "OS": lambda x: x[0] == "OpenStack",
          "kw": lambda x: x[1]["type"] == "kw", "para": lambda x: x[1]["type"] == "para", "mixed": lambda x: x[1]["type"] == "mixed",
          "zero_overlap(measured)": lambda x: not overlap[x[1]["id"]], "overlap(measured)": lambda x: bool(overlap[x[1]["id"]])}
for m in runs: summary[m] = {g: agg(m, f) for g, f in groups.items()}
os.makedirs(f"{HERE}/results", exist_ok=True)
json.dump(dict(overlap=overlap, summary=summary, per_query=res, runs=runs), open(f"{HERE}/results/r7e_eval.json", "w"), indent=0)
cols = ["cR@5", "cR@10", "R@10", "P@5", "MRR"]
print("zero-overlap queries:", [q for q, v in overlap.items() if not v])
for g in groups:
    print(f"\n== {g} ==   " + "  ".join(f"{c:>6}" for c in cols) + "  zero")
    for m in sorted(runs, key=lambda m: -summary[m][g]["MRR"]):
        if "[" in m and g != "ALL": continue
        print(f"{m:48s}" + "  ".join(f"{summary[m][g][c]:6.3f}" for c in cols) + f"  {summary[m][g]['zero']}")
