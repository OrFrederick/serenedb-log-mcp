"""Problem families: cluster BGL template embeddings, compare with Component and alert categories (ARI/NMI/purity).
Baselines: TF-IDF over template tokens (lexical), random assignment."""
import json, re, numpy as np, collections, sys
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import adjusted_rand_score as ARI, normalized_mutual_info_score as NMI
from sklearn.feature_extraction.text import TfidfVectorizer
CACHE = "/private/tmp/claude-501/-Users-frederick-Development-LogSearcher/bbdd15e1-b2d7-42b2-8c5d-c8e1bbcc9ff9/scratchpad/emb"
T = json.load(open("templates.json"))["bgl"]
txt = [re.sub(r"\s+", " ", t["template"].replace("<*>", " ")) for t in T]
comp = [t["component"] for t in T]
def alert_cat(t):
    nz = {k: v for k, v in t["alert_labels"].items() if k != "-"}
    return max(nz, key=nz.get) if nz and sum(nz.values()) >= 0.5 * t["occurrences"] else None
cat = [alert_cat(t) for t in T]; ai = [i for i, c in enumerate(cat) if c]
fam = [re.match(r"(KERN|APP|LINK|MON|MMCS|MAS)", c).group(1) for c in (cat[i] for i in ai)]
def purity(y, c):
    tot = 0
    for cl in set(c):
        tot += collections.Counter(y[i] for i in range(len(y)) if c[i] == cl).most_common(1)[0][1]
    return tot / len(y)
def clus(X, k):
    X = X / np.linalg.norm(X, axis=1, keepdims=True)
    return AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(X)
def score(X, name):
    out = {}
    k = len(set(comp)); c = clus(X, k); out["component(k=%d)" % k] = dict(ARI=ARI(comp, c), NMI=NMI(comp, c), purity=purity(comp, c))
    Xa = X[ai]; ya = [cat[i] for i in ai]
    k = len(set(ya)); c = clus(Xa, k); out["alert_cat(k=%d,n=%d)" % (k, len(ai))] = dict(ARI=ARI(ya, c), NMI=NMI(ya, c), purity=purity(ya, c))
    k = len(set(fam)); c = clus(Xa, k); out["alert_family(k=%d)" % k] = dict(ARI=ARI(fam, c), NMI=NMI(fam, c), purity=purity(fam, c))
    return {kk: {m: round(v, 3) for m, v in vv.items()} for kk, vv in out.items()}
res = {}
tf = TfidfVectorizer(token_pattern=r"[A-Za-z][A-Za-z0-9_]+", lowercase=True, sublinear_tf=True).fit_transform(txt).toarray() + 1e-9
res["tfidf (lexical)"] = score(tf, "tfidf")
rng = np.random.default_rng(0)
res["random"] = score(rng.normal(size=(len(T), 16)), "random")
for m in ["minilm-l6", "bge-small", "granite-small-r2", "nomic-v1.5", "jina-v2-code", "qwen3-emb-8b@rwth"]:
    res[m] = score(np.load(f"{CACHE}/{m}.npz")["bgl_docs"], m)
json.dump(res, open("families_results.json", "w"), indent=1)
for m, r in res.items(): print(f"{m:20s}", " | ".join(f"{k}: ARI {v['ARI']:.2f} NMI {v['NMI']:.2f} pur {v['purity']:.2f}" for k, v in r.items()))
# human sense-check: 25 clusters of all 320 BGL templates with the best local model vs tfidf
for name, X in [("granite-small-r2", np.load(f"{CACHE}/granite-small-r2.npz")["bgl_docs"]), ("tfidf", tf)]:
    c = clus(X, 25); lines = []
    for cl, n in collections.Counter(c).most_common():
        mem = [i for i in range(len(T)) if c[i] == cl]
        lines.append(f"## cluster {cl} ({n} templates; components {dict(collections.Counter(comp[i] for i in mem).most_common(3))})")
        lines += [f"- {T[i]['id']} [{comp[i]}|{cat[i] or '-'}] {T[i]['template'][:90]}" for i in mem[:8]]
        if n > 8: lines.append(f"- ... {n-8} more")
    open(f"families_clusters_{name}.md", "w").write("\n".join(lines) + "\n")
print("alert templates:", len(ai), collections.Counter(cat[i] for i in ai).most_common())
