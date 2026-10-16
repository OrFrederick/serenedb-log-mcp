"""Problem families: cluster template embeddings (k-means, agglomerative cosine/average) per Dataset and
compare with hand family labels, Component, and (BGL alert templates) alert_tag. TF-IDF baseline."""
import sys, os, glob, json, collections, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, doc_text, HERE
from r7e_families import fam
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.metrics import adjusted_rand_score as ARI, normalized_mutual_info_score as NMI
from sklearn.feature_extraction.text import TfidfVectorizer
R, _ = load()
def purity(lab, ref):
    tot = 0
    for c in set(lab):
        tot += collections.Counter(r for l, r in zip(lab, ref) if l == c).most_common(1)[0][1]
    return tot / len(lab)
def feats():
    out = {}
    for f in sorted(glob.glob(f"{HERE}/emb/*.npz")):
        out[os.path.basename(f)[:-4].split("__")[-1]] = np.load(f)["D_clean"]
    docs = [doc_text(r, "clean") for r in R]
    out["tfidf(baseline)"] = TfidfVectorizer(token_pattern=r"[A-Za-z][A-Za-z0-9_]+", sublinear_tf=True).fit_transform(docs).toarray()
    return out
F = feats(); res = {}; samples = {}
for ds, ks in (("BGL", (10, 15, 20)), ("OpenStack", (8, 10, 12))):
    rows = [i for i, r in enumerate(R) if r["dataset"] == ds]
    ref_f = [fam[f"{ds}:{R[i]['event_id']}"] for i in rows]
    ref_c = [R[i]["component"] for i in rows]
    alert_rows = [j for j, i in enumerate(rows) if R[i]["alert_tag"] not in (None, "-")]
    for name, X in F.items():
        Xd = X[rows]; Xn = Xd / np.maximum(np.linalg.norm(Xd, axis=1, keepdims=True), 1e-9)
        for k in ks:
            for algo in ("kmeans", "agglo"):
                lab = (KMeans(k, n_init=10, random_state=0).fit_predict(Xn) if algo == "kmeans"
                       else AgglomerativeClustering(k, metric="euclidean", linkage="average").fit_predict(Xn))
                r = dict(ARI_family=ARI(ref_f, lab), NMI_family=NMI(ref_f, lab), purity_family=purity(lab, ref_f),
                         NMI_component=NMI(ref_c, lab), ARI_component=ARI(ref_c, lab),
                         largest_cluster_frac=collections.Counter(lab).most_common(1)[0][1] / len(lab))
                if ds == "BGL" and alert_rows:
                    ra = [R[rows[j]]["alert_tag"] for j in alert_rows]; la = [lab[j] for j in alert_rows]
                    r |= dict(NMI_alert=NMI(ra, la), purity_alert=purity(la, ra), n_alert_tpl=len(alert_rows))
                res[f"{ds}|{name}|{algo}|k={k}"] = {a: round(float(b), 3) for a, b in r.items()}
                if (name, algo, k) in (("bge-small-en-v1.5", "kmeans", ks[1]), ("tfidf(baseline)", "kmeans", ks[1]), ("all-MiniLM-L6-v2", "kmeans", ks[1])):
                    cl = collections.defaultdict(list)
                    for j, l in enumerate(lab): cl[int(l)].append((R[rows[j]]["occurrences"], R[rows[j]]["event_id"], doc_text(R[rows[j]])[:60], ref_f[j]))
                    samples[f"{ds}|{name}|{algo}|k={k}"] = {str(l): dict(size=len(v), families=dict(collections.Counter(x[3] for x in v).most_common(3)), top=[f"{x[1]}: {x[2]}" for x in sorted(v, reverse=True)[:5]]) for l, v in sorted(cl.items())}
    # reference: how well does Component alone agree with the hand families
    res[f"{ds}|Component-as-clustering|-|-"] = dict(ARI_family=round(ARI(ref_f, ref_c), 3), NMI_family=round(NMI(ref_f, ref_c), 3), n_components=len(set(ref_c)))
json.dump(dict(scores=res, samples=samples), open(f"{HERE}/results/r7e_cluster.json", "w"), indent=1)
print(f"{'run':55s} ARI_f  NMI_f  pur_f  NMI_c  NMI_al pur_al big")
for k, v in res.items():
    print(f"{k:55s} " + " ".join(f"{v.get(x, float('nan')):5.3f}" for x in ("ARI_family", "NMI_family", "purity_family", "NMI_component", "NMI_alert", "purity_alert", "largest_cluster_frac")))
