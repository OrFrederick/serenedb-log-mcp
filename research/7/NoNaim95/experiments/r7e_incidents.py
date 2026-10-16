"""Similar past Incidents (small): gap-based Bursts per alert_tag (30-min gap), up to 8 largest Bursts per
alert_tag with >=3 Bursts. Signature = bag of Loghub-2.0 templates of all Log Entries on the Burst's nodes in
[start-5min, end+5min] + Components + spread. Task: nearest other Burst should have the same alert_tag.
Compare Jaccard(template sets), TF-IDF cosine over template bags, embedding of signature text. Two variants:
full signature, and context-only (alert-tagged lines removed -> can the surroundings tell the category?)."""
import sys, os, re, json, time, collections, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, doc_text, PREFIX, HERE
from fastembed import TextEmbedding
R, _ = load(); bgl_t = {r["event_id"]: r for r in R if r["dataset"] == "BGL"}
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
MASK = r"(0x)?[0-9a-fA-F]*[0-9][0-9a-fA-F]*"
# masked -> template map
def rx(t): return re.compile("^" + "(.*?)".join(re.escape(p) for p in t.split("<*>")) + "$", re.S)
cands = sorted(((len(t["template"].replace("<*>", "")), e, rx(t["template"])) for e, t in bgl_t.items()), reverse=True)
mm = c.execute(f"SELECT regexp_replace(message, '{MASK}', '#', 'g') AS m, min(message) FROM bgl GROUP BY m").fetchall()
mapping = {}
for m_, ex in mm:
    mapping[m_] = next((e for _, e, r in cands if r.match((ex or "").strip())), "UNK")
t0 = time.time()
bursts = c.execute("""
WITH a AS (SELECT alert_tag, epoch, line_no, node FROM bgl WHERE alert_tag <> '-'),
b AS (SELECT *, CASE WHEN epoch - LAG(epoch) OVER (PARTITION BY alert_tag ORDER BY epoch, line_no) <= 1800 THEN 0 ELSE 1 END AS new_burst FROM a),
c AS (SELECT *, SUM(new_burst) OVER (PARTITION BY alert_tag ORDER BY epoch, line_no ROWS UNBOUNDED PRECEDING) AS burst FROM b),
d AS (SELECT alert_tag, burst, min(epoch) AS t0, max(epoch) AS t1, count(*) AS n, count(DISTINCT node) AS nodes FROM c GROUP BY alert_tag, burst),
e AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY alert_tag ORDER BY n DESC, t0) AS rk, count(*) OVER (PARTITION BY alert_tag) AS nb FROM d)
SELECT alert_tag, burst, t0, t1, n, nodes, nb FROM e WHERE rk <= 8 AND nb >= 3 ORDER BY alert_tag, t0""").fetchall()
t_burst_sql = time.time() - t0
print(len(bursts), "bursts", collections.Counter(b[0] for b in bursts), f"burst SQL {t_burst_sql:.1f}s")
sigs = []
for tag, bid, t0_, t1_, n, nodes, nb in bursts:
    rows = c.execute(f"""SELECT node, component, alert_tag, regexp_replace(message, '{MASK}', '#', 'g') FROM bgl
        WHERE epoch BETWEEN {t0_ - 300} AND {t1_ + 300}
          AND node IN (SELECT DISTINCT node FROM bgl WHERE alert_tag = '{tag}' AND epoch BETWEEN {t0_} AND {t1_})""").fetchall()
    full = collections.Counter(mapping.get(r[3], "UNK") for r in rows)
    ctx = collections.Counter(mapping.get(r[3], "UNK") for r in rows if r[2] == "-")
    comps = collections.Counter(r[1] for r in rows)
    sigs.append(dict(tag=tag, t0=t0_, n=n, nodes=nodes, dur_min=round((t1_ - t0_) / 60, 1), full=full, ctx=ctx, comps=comps, n_ctx_lines=sum(ctx.values())))
def sig_text(s, key):
    top = [e for e, _ in s[key].most_common(8) if e != "UNK"]
    comp = ", ".join(k for k, _ in s["comps"].most_common(3))
    return f"components {comp}; {s['nodes']} nodes over {s['dur_min']} minutes; " + "; ".join(doc_text(bgl_t[e]) for e in top)
def evaluate(simfn, key):
    n = len(sigs); hits1 = hits3 = 0; rr = []
    for i in range(n):
        sims = sorted(((simfn(i, j), j) for j in range(n) if j != i), reverse=True)
        same = [sigs[j]["tag"] == sigs[i]["tag"] for _, j in sims]
        hits1 += same[0]; hits3 += sum(same[:3]) / 3
        rr.append(1 / (same.index(True) + 1) if True in same else 0)
    return dict(P1=round(hits1 / n, 3), P3=round(hits3 / n, 3), MRR=round(float(np.mean(rr)), 3))
res = {}
tags = [s["tag"] for s in sigs]; cnt = collections.Counter(tags)
res["random_baseline_P1"] = round(sum((cnt[t] - 1) / (len(tags) - 1) for t in tags) / len(tags), 3)
model = TextEmbedding("BAAI/bge-small-en-v1.5", cache_dir="/tmp/r7e_fe/bge-small")
for key in ("full", "ctx"):
    sets = [set(s[key]) - {"UNK"} for s in sigs]
    res[f"{key}|jaccard"] = evaluate(lambda i, j: len(sets[i] & sets[j]) / max(1, len(sets[i] | sets[j])), key)
    vocab = sorted(set().union(*sets)); vi = {e: k for k, e in enumerate(vocab)}
    df = collections.Counter(e for s in sets for e in s)
    V = np.zeros((len(sigs), len(vocab)))
    for i, s in enumerate(sigs):
        for e, cn in s[key].items():
            if e in vi: V[i, vi[e]] = (1 + np.log(cn)) * np.log(len(sigs) / df[e])
    V /= np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-9)
    res[f"{key}|tfidf_bag_cosine"] = evaluate(lambda i, j: float(V[i] @ V[j]), key)
    E = np.array(list(model.embed([sig_text(s, key) for s in sigs]))); E /= np.linalg.norm(E, axis=1, keepdims=True)
    res[f"{key}|embed_signature_bge_small"] = evaluate(lambda i, j: float(E[i] @ E[j]), key)
    top8 = [set(e for e, _ in s[key].most_common(8)) - {"UNK"} for s in sigs]
    res[f"{key}|jaccard_top8"] = evaluate(lambda i, j: len(top8[i] & top8[j]) / max(1, len(top8[i] | top8[j])), key)
    E2 = np.array(list(model.embed(["; ".join(doc_text(bgl_t[e]) for e, _ in s[key].most_common(8) if e != "UNK") or "none" for s in sigs]))); E2 /= np.linalg.norm(E2, axis=1, keepdims=True)
    res[f"{key}|embed_templates_only_top8"] = evaluate(lambda i, j: float(E2[i] @ E2[j]), key)
    meta = ["components " + ", ".join(k for k, _ in s["comps"].most_common(3)) + f"; {s['nodes']} nodes over {s['dur_min']} minutes" for s in sigs]
    E3 = np.array(list(model.embed(meta))); E3 /= np.linalg.norm(E3, axis=1, keepdims=True)
    cs = [set(k for k, _ in s["comps"].most_common(3)) for s in sigs]
    ln = np.log1p([s["nodes"] for s in sigs]); ld = np.log1p([s["dur_min"] for s in sigs])
    def structured(i, j):
        jt = len(sets[i] & sets[j]) / max(1, len(sets[i] | sets[j])); jc = len(cs[i] & cs[j]) / max(1, len(cs[i] | cs[j]))
        return jt + 0.5 * jc + 0.5 * (1 - abs(ln[i] - ln[j]) / ln.max()) + 0.5 * (1 - abs(ld[i] - ld[j]) / max(ld.max(), 1e-9))
    res[f"{key}|structured(jaccard+comp+log nodes+log dur, no embeddings)"] = evaluate(structured, key)
    res[f"{key}|embed_meta_only(no templates)"] = evaluate(lambda i, j: float(E3[i] @ E3[j]), key)
res["n_bursts"] = len(sigs); res["tags"] = dict(cnt); res["burst_sql_s"] = round(t_burst_sql, 2)
res["example_signature_text"] = sig_text(sigs[0], "full")[:400]
res["unk_share_full"] = round(sum(s["full"]["UNK"] for s in sigs) / max(1, sum(sum(s["full"].values()) for s in sigs)), 4)
print(json.dumps(res, indent=1)); json.dump(res, open(f"{HERE}/results/r7e_incidents.json", "w"), indent=1)
