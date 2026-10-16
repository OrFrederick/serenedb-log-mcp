"""Raw-line cost cliff: count distinct masked messages, measure local embedding throughput on a raw-line sample, extrapolate."""
import time, json, numpy as np, psycopg, os
CACHE = "/private/tmp/claude-501/-Users-frederick-Development-LogSearcher/bbdd15e1-b2d7-42b2-8c5d-c8e1bbcc9ff9/scratchpad/emb/fastembed_models"
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
res = {}
MASK = "regexp_replace(content, '[0-9a-fA-Fx]*[0-9][0-9a-fA-Fx]*', '#', 'g')"
for tbl in ("bgl", "openstack"):
    t0 = time.time()
    n, nd, ndm = c.execute(f"SELECT count(*), count(DISTINCT content), count(DISTINCT {MASK}) FROM {tbl}").fetchone()
    res[tbl] = dict(lines=n, distinct_raw=nd, distinct_masked=ndm, sql_s=round(time.time() - t0, 1))
    print(tbl, res[tbl], flush=True)
sample = [r[0] for r in c.execute("SELECT content FROM bgl WHERE line_no % 2371 = 0 LIMIT 2000").fetchall()]
res["sample_lines"] = len(sample); res["sample_mean_chars"] = round(float(np.mean([len(s) for s in sample])), 1)
from fastembed import TextEmbedding
for mid, name, dim in [("sentence-transformers/all-MiniLM-L6-v2", "minilm-l6", 384), ("ibm-granite/granite-embedding-small-english-r2", "granite-small-r2", 384)]:
    m = TextEmbedding(mid, cache_dir=CACHE, threads=8); list(m.embed(sample[:64]))
    t0 = time.time(); list(m.embed(sample, batch_size=128)); dt = time.time() - t0
    lps = len(sample) / dt
    res[name] = dict(lines_per_s=round(lps, 1), dim=dim,
        bgl_all_hours=round(res["bgl"]["lines"] / lps / 3600, 2), bgl_masked_distinct_min=round(res["bgl"]["distinct_masked"] / lps / 60, 2),
        bgl_all_storage_GB_float32=round(res["bgl"]["lines"] * dim * 4 / 1e9, 2), bgl_all_storage_GB_sq8=round(res["bgl"]["lines"] * dim / 1e9, 2))
    print(name, res[name], flush=True)
# hosted (RWTH qwen3-embedding-8b, 4096 dims): measured 368 templates in 11.6 s (embed.py) and 10.0 s via ai_embed
hosted_lps = 368 / 10.0
res["qwen3-emb-8b@rwth"] = dict(lines_per_s=round(hosted_lps, 1), dim=4096, bgl_all_hours=round(res["bgl"]["lines"] / hosted_lps / 3600, 1),
    bgl_masked_distinct_min=round(res["bgl"]["distinct_masked"] / hosted_lps / 60, 1), bgl_all_storage_GB_float32=round(res["bgl"]["lines"] * 4096 * 4 / 1e9, 1))
json.dump(res, open("raw_cost.json", "w"), indent=1); print(res["qwen3-emb-8b@rwth"])
