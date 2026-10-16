"""Embed templates + labelled queries with several models; cache vectors and timing.
Local: fastembed (ONNX). Hosted (free for us): RWTHgpt / KI:connect OpenAI-compatible /embeddings.
Outputs: $CACHE/<model>.npz and models_timing.json (in this dir)."""
import os, json, time, re, sys, pathlib, yaml, numpy as np, statistics as st
CACHE = pathlib.Path(os.environ.get("R7_CACHE", "/private/tmp/claude-501/-Users-frederick-Development-LogSearcher/bbdd15e1-b2d7-42b2-8c5d-c8e1bbcc9ff9/scratchpad/emb")); CACHE.mkdir(parents=True, exist_ok=True)
FE_CACHE = CACHE / "fastembed_models"
T = json.load(open("templates.json")); L = yaml.safe_load(open("labels.yaml"))
def doc_text(t): return re.sub(r"\s+", " ", t["template"].replace("<*>", " ")).strip()
docs = {ds: [doc_text(t) for t in T[ds]] for ds in ("bgl", "openstack")}
queries = {ds: [q["q"] for q in L[ds]] for ds in ("bgl", "openstack")}

# name -> (backend, model id, query prefix, doc prefix)
MODELS = {
  "minilm-l6":      ("fe", "sentence-transformers/all-MiniLM-L6-v2", "", ""),
  "bge-small":      ("fe", "BAAI/bge-small-en-v1.5", "Represent this sentence for searching relevant passages: ", ""),
  "bge-base":       ("fe", "BAAI/bge-base-en-v1.5", "Represent this sentence for searching relevant passages: ", ""),
  "e5-small-v2":    ("fe-custom", "Xenova/e5-small-v2", "query: ", "passage: "),
  "e5-base-v2":     ("fe-custom", "Xenova/e5-base-v2", "query: ", "passage: "),
  "nomic-v1.5":     ("fe", "nomic-ai/nomic-embed-text-v1.5", "search_query: ", "search_document: "),
  "arctic-s":       ("fe", "snowflake/snowflake-arctic-embed-s", "Represent this sentence for searching relevant passages: ", ""),
  "granite-small-r2": ("fe", "ibm-granite/granite-embedding-small-english-r2", "", ""),
  "jina-v2-code":   ("fe", "jinaai/jina-embeddings-v2-base-code", "", ""),
  "potion-retr-32M": ("fe", "minishlab/potion-retrieval-32M", "", ""),
  "qwen3-emb-8b@rwth": ("rwth", "qwen3-embedding-8b", "Instruct: Given a question about system logs, retrieve log message templates that are evidence for it\nQuery: ", ""),
  "e5-mistral-7b@rwth": ("rwth", "e5-mistral-7b-instruct", "Instruct: Given a question about system logs, retrieve log message templates that are evidence for it\nQuery: ", ""),
}

def dir_mb(p):
    return sum(f.stat().st_size for f in pathlib.Path(p).rglob("*") if f.is_file() and not f.is_symlink()) / 1e6 if pathlib.Path(p).exists() else 0

def rwth_embed(model, texts):
    import httpx
    key = os.environ["RWTHGPT_API_KEY"]
    out = []
    for i in range(0, len(texts), 32):
        for attempt in range(6):
            r = httpx.post("https://chat.kiconnect.nrw/api/v1/embeddings", json={"model": model, "input": texts[i:i+32]},
                           headers={"Authorization": f"Bearer {key}"}, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt * 2); continue
            r.raise_for_status(); break
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        out += [d["embedding"] for d in data]
    return np.array(out, dtype=np.float32)

def run(name):
    backend, mid, qp, dp = MODELS[name]
    rec = {"name": name, "model": mid, "backend": backend}
    if backend.startswith("fe"):
        from fastembed import TextEmbedding
        if backend == "fe-custom":
            from fastembed.common.model_description import PoolingType, ModelSource
            try:
                TextEmbedding.add_custom_model(model=mid, pooling=PoolingType.MEAN, normalization=True,
                    sources=ModelSource(hf=mid), dim=384 if "small" in mid else 768, model_file="onnx/model.onnx")
            except ValueError: pass
        before = dir_mb(FE_CACHE); t0 = time.time()
        m = TextEmbedding(mid, cache_dir=str(FE_CACHE), threads=4)
        rec["load_s"] = round(time.time() - t0, 2)
        rec["download_mb"] = round(dir_mb(FE_CACHE) - before, 1)
        enc = lambda xs: np.array(list(m.embed(xs, batch_size=64)), dtype=np.float32)
    else:
        enc = lambda xs: rwth_embed(mid, xs); rec["load_s"] = 0; rec["download_mb"] = 0
    res = {}
    t0 = time.time()
    for ds in docs: res[f"{ds}_docs"] = enc([dp + d for d in docs[ds]])
    rec["docs_s"] = round(time.time() - t0, 2); rec["n_docs"] = sum(len(v) for v in docs.values())
    lat = []
    for ds in queries:
        vs = []
        for q in queries[ds]:
            t1 = time.perf_counter(); vs.append(enc([qp + q])[0]); lat.append(time.perf_counter() - t1)
        res[f"{ds}_q"] = np.array(vs)
    rec["query_ms_median"] = round(st.median(lat) * 1000, 1); rec["query_ms_p90"] = round(sorted(lat)[int(.9*len(lat))] * 1000, 1)
    rec["dim"] = int(res["bgl_docs"].shape[1])
    np.savez(CACHE / f"{name}.npz", **res)
    return rec

if __name__ == "__main__":
    names = sys.argv[1:] or list(MODELS)
    tim = json.load(open("models_timing.json")) if os.path.exists("models_timing.json") else {}
    for n in names:
        try:
            tim[n] = run(n); print(tim[n], flush=True)
        except Exception as e:
            print("FAIL", n, repr(e)[:300], flush=True); tim[n] = {"name": n, "error": repr(e)[:300]}
        json.dump(tim, open("models_timing.json", "w"), indent=1)
