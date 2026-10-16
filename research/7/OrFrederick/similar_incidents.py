"""Similar past Incidents (BGL, #1 definition: key alert_label, gap 300 s -> 1,202 Incidents).
Signature = set of templates of the Incident's lines + Component + Spread. For each Incident, find the nearest EARLIER
Incident; a hit means it has the same alert category (a real repeat). Methods: Jaccard over template sets,
embedding of the signature text (cosine), spread-only, random. Note: alert categories were assigned by
message rules, so templates carry the category almost by construction; this tests a ceiling, not a hard task."""
import json, re, sys, os, numpy as np, collections
CACHE = "/private/tmp/claude-501/-Users-frederick-Development-LogSearcher/bbdd15e1-b2d7-42b2-8c5d-c8e1bbcc9ff9/scratchpad"
I = json.load(open(f"{CACHE}/incidents.json")); T = json.load(open("templates.json"))["bgl"]
mask = lambda s: re.sub(r"[0-9a-fA-Fx]*[0-9][0-9a-fA-Fx]*", "#", s)
pats = []
for t in T:
    parts = [re.escape(mask(p)) for p in t["template"].split("<*>")]
    pats.append((t["id"], re.compile("^\\s*" + ".*".join(parts) + "\\s*$"), len(t["template"].replace("<*>", ""))))
pats.sort(key=lambda x: -x[2])
cache = {}
def tpl(msg):
    if msg not in cache: cache[msg] = next((i for i, p, _ in pats if p.match(msg)), "UNK:" + msg[:60])
    return cache[msg]
for x in I:
    x["tpls"] = sorted({tpl(m[1]) for m in x["msgs"]}); x["comp"] = sorted({m[0] for m in x["msgs"]})
unk = sum(1 for x in I for t in x["tpls"] if t.startswith("UNK")); print("incidents", len(I), "unmatched template refs", unk)
I.sort(key=lambda x: x["start"])
tt = {t["id"]: t["template"] for t in T}
def sig_text(x):
    shape = "single node" if x["nodes"] == 1 else ("machine-wide" if x["midplanes"] >= 64 else f"{x['midplanes']} midplanes")
    return f"component {', '.join(x['comp'])}. spread {shape}. messages: " + " | ".join(tt.get(t, t[4:]).replace("<*>", " ") for t in x["tpls"])
texts = [sig_text(x) for x in I]
cats = [x["cat"] for x in I]
def eval_sim(S, name):
    hit = n = 0; first = []; fp_first = 0
    for i in range(1, len(I)):
        prev_same = any(cats[j] == cats[i] for j in range(i))
        sc = S[i, :i]; best = sc.max(); cand = np.where(sc == best)[0]; j = cand.max()  # most recent among ties
        if prev_same:
            n += 1; hit += cats[j] == cats[i]
        else:
            first.append(float(best))
    return dict(method=name, hit_at_1=round(hit / n, 3), n_with_prior_repeat=n, n_first_of_category=len(first),
                first_of_cat_median_top_score=round(float(np.median(first)), 3) if first else None,
                first_of_cat_share_with_score_gt0=round(float(np.mean(np.array(first) > 0)), 3) if first else None)
res = []
sets = [set(x["tpls"]) for x in I]
J = np.array([[len(a & b) / len(a | b) for b in sets] for a in sets]); res.append(eval_sim(J, "jaccard(template set)"))
sp = np.array([[np.log1p(x["lines"]), np.log1p(x["nodes"]), np.log1p(x["midplanes"]), np.log1p(x["dur"])] for x in I])
sp = (sp - sp.mean(0)) / sp.std(0); D = ((sp[:, None] - sp[None]) ** 2).sum(-1); res.append(eval_sim(-D, "spread only"))
rng = np.random.default_rng(0); res.append(eval_sim(rng.random((len(I), len(I))), "random"))
from fastembed import TextEmbedding
for mid, name in [("ibm-granite/granite-embedding-small-english-r2", "granite-small-r2"), ("sentence-transformers/all-MiniLM-L6-v2", "minilm-l6")]:
    m = TextEmbedding(mid, cache_dir=f"{CACHE}/emb/fastembed_models")
    E = np.array(list(m.embed(texts))); E /= np.linalg.norm(E, axis=1, keepdims=True)
    res.append(eval_sim(E @ E.T, f"embedding:{name}"))
    if name == "granite-small-r2":
        # cross-category look-alikes: nearest OTHER category by mean signature embedding (embeddings can suggest; Jaccard says 0)
        cs = sorted(set(cats)); C = np.array([E[[i for i in range(len(I)) if cats[i] == c]].mean(0) for c in cs]); C /= np.linalg.norm(C, axis=1, keepdims=True)
        S = C @ C.T; np.fill_diagonal(S, -1)
        cats_sets = {c: set().union(*[sets[i] for i in range(len(I)) if cats[i] == c]) for c in cs}
        look = [dict(category=c, nearest_other=cs[int(S[k].argmax())], cosine=round(float(S[k].max()), 3),
                     jaccard_templates=round(len(cats_sets[c] & cats_sets[cs[int(S[k].argmax())]]) / len(cats_sets[c] | cats_sets[cs[int(S[k].argmax())]]), 3)) for k, c in enumerate(cs)]
E2 = None
out = dict(n_incidents=len(I), results=res, cross_category_lookalikes=look, example_signature=texts[0])
json.dump(out, open("similar_incidents_results.json", "w"), indent=1)
for r in res: print(r)
for l in look: print(l)
