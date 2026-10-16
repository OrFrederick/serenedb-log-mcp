"""Load the 368 templates (+ bge-small & MiniLM vectors) into SereneDB table r7e_tpl with one
inverted index holding full-text (stemmed BM25) and two IVF cosine vector columns."""
import sys, os, time, numpy as np, psycopg
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from r7e_common import load, doc_text, HERE
R, L = load()
E1 = np.load(f"{HERE}/emb/BAAI__bge-small-en-v1.5.npz")["D_clean"]
E2 = np.load(f"{HERE}/emb/sentence-transformers__all-MiniLM-L6-v2.npz")["D_clean"]
vec = lambda v: "[" + ",".join(f"{x:.6f}" for x in v) + "]"
with psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True) as c:
    c.execute("DROP TABLE IF EXISTS r7e_tpl")
    c.execute("CREATE TABLE r7e_tpl (id INTEGER, dataset VARCHAR, event_id VARCHAR, body VARCHAR, n BIGINT, e_bge FLOAT[384], e_mini FLOAT[384])")
    t0 = time.time()
    for i, r in enumerate(R):
        c.execute(f"INSERT INTO r7e_tpl VALUES (%s,%s,%s,%s,%s,'{vec(E1[i])}'::FLOAT[384],'{vec(E2[i])}'::FLOAT[384])",
                  (i, r["dataset"], r["event_id"], doc_text(r, "clean"), r["occurrences"]))
    print("insert", round(time.time() - t0, 2), "s")
    t0 = time.time()
    c.execute("CREATE INDEX r7e_tpl_idx ON r7e_tpl USING inverted (id, dataset, event_id, body r7e_en, e_bge ivf(metric = 'cosine'), e_mini ivf(metric = 'cosine'))")
    c.execute("VACUUM (REFRESH_TABLE) r7e_tpl")
    print("index+refresh", round(time.time() - t0, 2), "s")
    print(c.execute("SELECT dataset, count(*) FROM r7e_tpl_idx GROUP BY dataset").fetchall())
