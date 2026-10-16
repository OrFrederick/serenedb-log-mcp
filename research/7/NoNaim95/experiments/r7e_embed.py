"""Embed templates + queries with one fastembed model; record download MB, cold start, latencies.
usage: r7e_embed.py <model> <cache_dir>   -> writes emb/<safe>.npz + appends emb/timings.jsonl"""
import sys, os, time, json, subprocess, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, doc_text, PREFIX, HERE
model, cache = sys.argv[1], sys.argv[2]
def du(p):
    return sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(p) for f in fs if not os.path.islink(os.path.join(a, f))) / 1e6 if os.path.exists(p) else 0
before = du(cache)
t0 = time.time()
from fastembed import TextEmbedding
t_import = time.time() - t0
t0 = time.time(); m = TextEmbedding(model, cache_dir=cache); t_init = time.time() - t0
dl = du(cache) - before
R, L = load(); qp, dp = PREFIX.get(model, ("", ""))
res = {}
for variant in ["raw", "clean", "meta"]:
    docs = [dp + doc_text(r, variant) for r in R]
    t0 = time.time(); D = np.array(list(m.embed(docs, batch_size=64))); td = time.time() - t0
    res[f"D_{variant}"] = D; res[f"t_docs_{variant}"] = td
qs = [(ds, q["id"], q["text"]) for ds in L for q in L[ds] if ds in ("BGL", "OpenStack")]
lat = []
for _ in range(2):  # 2 passes, keep the second (warm)
    lat = []; Q = []
    for ds, qid, t in qs:
        t0 = time.time(); v = np.array(list(m.embed([qp + t])))[0]; lat.append((time.time() - t0) * 1000); Q.append(v)
os.makedirs(f"{HERE}/emb", exist_ok=True)
safe = model.replace("/", "__")
np.savez(f"{HERE}/emb/{safe}.npz", Q=np.array(Q), qids=np.array([q[1] for q in qs]), **{k: v for k, v in res.items() if k.startswith("D_")})
rec = dict(loadavg=os.getloadavg()[0], model=model, dim=int(res["D_clean"].shape[1]), download_MB=round(dl, 1), import_s=round(t_import, 2), init_s=round(t_init, 2),
           docs368_s={k[7:]: round(v, 3) for k, v in res.items() if k.startswith("t_docs")}, q_ms_median=round(float(np.median(lat)), 2), q_ms_p95=round(float(np.percentile(lat, 95)), 2))
print(json.dumps(rec)); open(f"{HERE}/emb/timings.jsonl", "a").write(json.dumps(rec) + "\n")
