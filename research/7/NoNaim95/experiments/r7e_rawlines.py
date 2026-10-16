"""Raw-line cost cliff: throughput on 20k distinct raw BGL messages; embed all distinct masked messages;
check if message-level retrieval (mapped back to templates) changes answers vs template-level retrieval."""
import sys, os, re, time, json, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, metrics, PREFIX, doc_text, HERE
from fastembed import TextEmbedding
R, L = load()
MODEL = sys.argv[1] if len(sys.argv) > 1 else "sentence-transformers/all-MiniLM-L6-v2"
NTHR = 20000
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
MASK = r"(0x)?[0-9a-fA-F]*[0-9][0-9a-fA-F]*"
t0 = time.time()
masked = c.execute(f"SELECT regexp_replace(message, '{MASK}', '#', 'g') AS m, count(*) AS n, min(message) AS ex FROM bgl WHERE message IS NOT NULL GROUP BY m").fetchall()
t_mask_sql = time.time() - t0
sample = [r[0] for r in c.execute(f"SELECT message FROM (SELECT DISTINCT message FROM bgl WHERE message IS NOT NULL) t LIMIT {NTHR}").fetchall()]
m = TextEmbedding(MODEL, cache_dir=f"/tmp/r7e_fe/{MODEL.replace('/', '_')}" if "bge-small" not in MODEL else "/tmp/r7e_fe/bge-small")
qp, dp = PREFIX.get(MODEL, ("", ""))
list(m.embed(["warmup"] * 8))
t0 = time.time(); X = np.array(list(m.embed([dp + s for s in sample], batch_size=256))); t_thr = time.time() - t0
lps = len(sample) / t_thr
load1 = os.getloadavg()[0]
t0 = time.time(); M = np.array(list(m.embed([dp + r[0] for r in masked], batch_size=256))); t_masked = time.time() - t0
# map masked messages -> Loghub-2.0 BGL templates via template regexes (longest literal first)
bgl_t = [r for r in R if r["dataset"] == "BGL"]
def rx(t):
    parts = [re.escape(p) for p in t.split("<*>")]
    return re.compile("^" + "(.*?)".join(parts) + "$", re.S)
cands = sorted(((len(t["template"].replace("<*>", "")), t["event_id"], rx(t["template"])) for t in bgl_t), reverse=True)
def to_tpl(msg):
    for _, e, r in cands:
        if r.match((msg or "").strip()): return e
    return None
mapped = [to_tpl(r[2]) for r in masked]
cov_masked = sum(x is not None for x in mapped) / len(mapped)
cov_lines = sum(r[1] for r, x in zip(masked, mapped) if x is not None) / sum(r[1] for r in masked)
Mn = M / np.linalg.norm(M, axis=1, keepdims=True)
Dt = np.array(list(m.embed([dp + doc_text(t) for t in bgl_t]))); Dt /= np.linalg.norm(Dt, axis=1, keepdims=True)
per = {}
for q in L["BGL"]:
    qv = np.array(list(m.embed([qp + q["text"]])))[0]; qv /= np.linalg.norm(qv)
    order = np.argsort(-(Mn @ qv))[:100]
    via_msg = []
    for o in order:
        e = mapped[o]
        if e and e not in via_msg: via_msg.append(e)
    tpl = [bgl_t[o]["event_id"] for o in np.argsort(-(Dt @ qv))[:20]]
    per[q["id"]] = dict(msg=metrics(via_msg, q["relevant"]), tpl=metrics(tpl, q["relevant"]),
                        only_via_msg_top10=sorted(set(via_msg[:10]) - set(tpl[:10])), rel_only_via_msg=sorted((set(via_msg[:10]) - set(tpl[:10])) & set(q["relevant"])),
                        rel_only_via_tpl=sorted((set(tpl[:10]) - set(via_msg[:10])) & set(q["relevant"])))
agg = {k: {x: round(float(np.mean([v[k][x] for v in per.values()])), 3) for x in ("cR@5", "cR@10", "P@5", "MRR")} for k in ("msg", "tpl")}
total_lines = 4747963; distinct = c.execute("SELECT count(DISTINCT message) FROM bgl").fetchone()[0]
dim = X.shape[1]
out = dict(model=MODEL, dim=dim, loadavg=load1, throughput_lines_per_s=round(lps, 1), n_thr=len(sample), t_thr_s=round(t_thr, 1),
           n_masked=len(masked), t_embed_all_masked_s=round(t_masked, 1), t_mask_groupby_sql_s=round(t_mask_sql, 2),
           extrapolated_hours=dict(masked=round(len(masked) / lps / 3600, 3), distinct_raw=round(distinct / lps / 3600, 2), raw_lines=round(total_lines / lps / 3600, 1)),
           storage_GB_float32=dict(masked=round(len(masked) * dim * 4 / 1e9, 4), distinct_raw=round(distinct * dim * 4 / 1e9, 2), raw_lines=round(total_lines * dim * 4 / 1e9, 2)),
           n_distinct_raw=distinct, map_coverage_masked=round(cov_masked, 3), map_coverage_lines=round(cov_lines, 4),
           mean=agg, n_queries_with_rel_only_via_msg=sum(bool(v["rel_only_via_msg"]) for v in per.values()))
print(json.dumps(out, indent=1))
for qid, v in per.items():
    if v["rel_only_via_msg"] or v["rel_only_via_tpl"]: print(qid, "msg-only rel:", v["rel_only_via_msg"], "tpl-only rel:", v["rel_only_via_tpl"])
json.dump(out | {"per_query": per}, open(f"{HERE}/results/r7e_rawlines_{MODEL.split('/')[-1]}.json", "w"), indent=0)
