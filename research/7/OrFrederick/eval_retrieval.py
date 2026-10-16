"""Retrieval comparison over templates: BM25 (SereneDB), BM25+synonyms, BM25+LLM keywords, vector (exact cosine), hybrid RRF."""
import os, re, json, time, yaml, numpy as np, psycopg, statistics as st, sys
CACHE = os.environ.get("R7_CACHE", "/private/tmp/claude-501/-Users-frederick-Development-LogSearcher/bbdd15e1-b2d7-42b2-8c5d-c8e1bbcc9ff9/scratchpad/emb")
T = json.load(open("templates.json")); L = yaml.safe_load(open("labels.yaml"))
SYN = yaml.safe_load(open("synonyms.yaml")); LLM = json.load(open("llm_expansions.json")); SEL = json.load(open("llm_select_gpt-oss-120b.json"))
STOP = set("a an the is are was were be been of on in to for and or not no with by at from how much many what which that this there it its did do does long left something were".split())
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
def toks(s): return [w for w in re.findall(r"[a-z0-9_]+", s.lower()) if w not in STOP and len(w) > 1]
bm25_lat = []
def bm25(ds, words, k=50):
    words = list(dict.fromkeys(words))
    if not words: return []
    t0 = time.perf_counter()
    rows = c.execute("SELECT template_id, BM25(r7_templates_idx.tableoid) s FROM r7_templates_idx WHERE dataset = %s AND body @@ to_tsquery(%s) ORDER BY s DESC, template_id LIMIT %s",
                     (ds, " ".join(words), k)).fetchall()
    bm25_lat.append(time.perf_counter() - t0)
    return [r[0] for r in rows]
def syn_expand(q): 
    w = toks(q); return w + [x for t in w for s in SYN.get(t, []) for x in toks(str(s))]
def rrf(*lists, k=60):
    sc = {}
    for l in lists:
        for i, d in enumerate(l): sc[d] = sc.get(d, 0) + 1 / (k + i + 1)
    return [d for d, _ in sorted(sc.items(), key=lambda x: -x[1])]
def metrics(ranked, rel):
    rel = set(rel); r5 = len(set(ranked[:5]) & rel) / len(rel); r10 = len(set(ranked[:10]) & rel) / len(rel)
    p5 = len(set(ranked[:5]) & rel) / 5
    mrr = next((1 / (i + 1) for i, d in enumerate(ranked) if d in rel), 0)
    return dict(R5=r5, R10=r10, P5=p5, MRR=mrr)
ids = {ds: [t["id"] for t in T[ds]] for ds in T}
models = [m for m in json.load(open("models_timing.json")) if os.path.exists(f"{CACHE}/{m}.npz")]
vec_rank = {}
for m in models:
    z = np.load(f"{CACHE}/{m}.npz")
    for ds in ids:
        D = z[f"{ds}_docs"]; Q = z[f"{ds}_q"]
        D = D / np.linalg.norm(D, axis=1, keepdims=True); Q = Q / np.linalg.norm(Q, axis=1, keepdims=True)
        S = Q @ D.T
        for qi in range(len(Q)): vec_rank[(m, ds, qi)] = [ids[ds][j] for j in np.argsort(-S[qi])[:50]]
results = {}  # method -> ds -> list of (qi, metrics)
per_query = []
gap = {}
for ds in ("bgl", "openstack"):
    for qi, item in enumerate(L[ds]):
        q = item["q"]
        runs = {"bm25": bm25(ds, toks(q)), "bm25+syn": bm25(ds, syn_expand(q)),
                "bm25+llm": bm25(ds, toks(q) + toks(LLM[f"{ds}::{q}"])),
                "llm-select(full catalogue)": SEL[f"{ds}::{q}"]}
        runs["rrf(bm25+syn,granite-small-r2)"] = rrf(runs["bm25+syn"], vec_rank[("granite-small-r2", ds, qi)])
        for m in models:
            runs[f"vec:{m}"] = vec_rank[(m, ds, qi)]
            runs[f"rrf(bm25,{m})"] = rrf(runs["bm25"], vec_rank[(m, ds, qi)])
            runs[f"rrf(bm25+llm,{m})"] = rrf(runs["bm25+llm"], vec_rank[(m, ds, qi)])
        if item.get("no_relevant"):
            per_query.append(dict(ds=ds, q=q, null=True, top3={k: v[:3] for k, v in runs.items() if k in ("bm25", "bm25+syn", "bm25+llm", "llm-select(full catalogue)") or k.startswith("vec:")}))
            continue
        gap[(ds, qi)] = not (set(runs["bm25"]) & set(item["rel"]))  # plain BM25 finds no relevant template at all
        pq = dict(ds=ds, q=q, kind=item["kind"], n_rel=len(item["rel"]), lexical_gap=gap[(ds, qi)])
        for meth, ranked in runs.items():
            mt = metrics(ranked, item["rel"]); results.setdefault(meth, {}).setdefault(ds, []).append(((ds, qi, item["kind"]), mt)); pq[meth] = mt
        per_query.append(pq)
def agg(lst): return {k: round(st.mean(x[1][k] for x in lst), 3) for k in ("R5", "R10", "P5", "MRR")} if lst else {}
table = {}
for meth, byds in results.items():
    allq = byds["bgl"] + byds["openstack"]
    table[meth] = {"bgl": agg(byds["bgl"]), "openstack": agg(byds["openstack"]), "all": agg(allq),
                   "paraphrase": agg([x for x in allq if x[0][2] == "paraphrase"]),
                   "keyword": agg([x for x in allq if x[0][2] == "keyword"]),
                   "lexical_gap": agg([x for x in allq if gap[(x[0][0], x[0][1])]])}
summary = dict(n_queries={ds: sum(1 for i in L[ds] if not i.get("no_relevant")) for ds in L}, n_gap=sum(gap.values()),
               bm25_ms_median=round(st.median(bm25_lat) * 1000, 2), table=table)
json.dump(summary, open("retrieval_results.json", "w"), indent=1)
json.dump(per_query, open("retrieval_per_query.json", "w"), indent=1)
print("queries", summary["n_queries"], "lexical-gap queries", summary["n_gap"], "bm25 ms", summary["bm25_ms_median"])
print(f"{'method':38s} {'R@5':>6} {'R@10':>6} {'P@5':>6} {'MRR':>6} | bgl R@10 | os R@10 | para R@10 | gap R@10")
for meth, r in sorted(table.items(), key=lambda x: -x[1]["all"]["R10"]):
    a = r["all"]; print(f"{meth:38s} {a['R5']:6.3f} {a['R10']:6.3f} {a['P5']:6.3f} {a['MRR']:6.3f} | {r['bgl']['R10']:.3f}    | {r['openstack']['R10']:.3f}   | {r['paraphrase']['R10']:.3f}     | {r['lexical_gap'].get('R10', float('nan')):.3f}")

# paired bootstrap of R@10 differences (all non-null queries)
rng = np.random.default_rng(0)
def boot(a, b):
    x = np.array([m["R10"] for _, m in results[a]["bgl"] + results[a]["openstack"]]); y = np.array([m["R10"] for _, m in results[b]["bgl"] + results[b]["openstack"]])
    d = x - y; bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(5000)]
    return dict(diff=round(d.mean(), 3), ci95=[round(float(np.percentile(bs, 2.5)), 3), round(float(np.percentile(bs, 97.5)), 3)])
pairs = [("vec:granite-small-r2", "bm25"), ("vec:minilm-l6", "bm25"), ("bm25+syn", "bm25"), ("vec:granite-small-r2", "bm25+syn"),
         ("rrf(bm25+llm,granite-small-r2)", "vec:granite-small-r2"), ("vec:qwen3-emb-8b@rwth", "vec:granite-small-r2"),
         ("llm-select(full catalogue)", "vec:granite-small-r2"), ("llm-select(full catalogue)", "bm25+syn"), ("rrf(bm25+syn,granite-small-r2)", "bm25+syn")]
summary["bootstrap_R10"] = {f"{a} - {b}": boot(a, b) for a, b in pairs}
json.dump(summary, open("retrieval_results.json", "w"), indent=1)
for k, v in summary["bootstrap_R10"].items(): print(k, v)
