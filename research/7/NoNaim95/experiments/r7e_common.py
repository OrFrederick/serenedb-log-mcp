import json, re, yaml, os
HERE = os.path.dirname(os.path.abspath(__file__))
def load():
    R = json.load(open(f"{HERE}/r7e_templates.json")); L = yaml.safe_load(open(f"{HERE}/r7e_labels.yaml"))
    return R, L
def clean(t):
    t = t.replace("<*>", " ")
    t = re.sub(r"\.{3,}", " ", t)
    return re.sub(r"\s+", " ", t).strip()
def doc_text(r, variant="clean"):
    if variant == "raw": return r["template"]
    if variant == "clean": return clean(r["template"])
    if variant == "meta": return f"{r['component']} {r['level']}: {clean(r['template'])}"
# per-model prefixes (from the model cards)
PREFIX = {
    "BAAI/bge-small-en-v1.5": ("Represent this sentence for searching relevant passages: ", ""),
    "BAAI/bge-base-en-v1.5": ("Represent this sentence for searching relevant passages: ", ""),
    "snowflake/snowflake-arctic-embed-s": ("Represent this sentence for searching relevant passages: ", ""),
    "nomic-ai/nomic-embed-text-v1.5-Q": ("search_query: ", "search_document: "),
    "nomic-ai/nomic-embed-text-v1.5": ("search_query: ", "search_document: "),
}
def metrics(ranked, rel, ks=(5, 10)):
    rel = set(rel); out = {}
    for k in ks:
        h = len(set(ranked[:k]) & rel)
        out[f"R@{k}"] = h / len(rel); out[f"cR@{k}"] = h / min(k, len(rel))
    out["P@5"] = len(set(ranked[:5]) & rel) / 5
    rr = 0.0
    for i, e in enumerate(ranked):
        if e in rel: rr = 1 / (i + 1); break
    out["MRR"] = rr
    return out
