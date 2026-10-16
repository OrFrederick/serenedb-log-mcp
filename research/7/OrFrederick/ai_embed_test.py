"""Test SereneDB ai_embed against RWTHgpt (OpenAI-compatible). Key read from env, never printed; secret dropped after."""
import os, time, psycopg, json
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
key = os.environ["RWTHGPT_API_KEY"].replace("'", "''")
log = {}
def run(label, sql, params=None, show=True):
    t0 = time.perf_counter()
    try:
        cur = c.execute(sql, params); rows = cur.fetchall() if cur.description else None
        dt = round((time.perf_counter() - t0) * 1000, 1); log[label] = {"ok": True, "ms": dt, "rows": str(rows)[:300] if show else None}
    except Exception as e:
        log[label] = {"ok": False, "err": str(e).splitlines()[0][:300]}
    print(label, log[label], flush=True)
try:
    run("create_secret", f"CREATE SECRET r7_rwth (TYPE openai, base_url 'https://chat.kiconnect.nrw/api', api_key '{key}')", show=False)
    if not log["create_secret"]["ok"]:
        print("secret error text (key redacted):", log["create_secret"]["err"].replace(key, "***"))
    run("list_secret", "SELECT name, type FROM sdb_secrets() WHERE name = 'r7_rwth'")
    run("embed_one", "SELECT array_length(ai_embed('memory problems', 'qwen3-embedding-8b', 'r7_rwth'), 1)")
    run("embed_one_again", "SELECT array_length(ai_embed('network issues', 'qwen3-embedding-8b', 'r7_rwth'), 1)")
    run("embed_templates_bgl", "CREATE TABLE r7_emb_qwen AS SELECT dataset, template_id, body, ai_embed(body, 'qwen3-embedding-8b', 'r7_rwth')::FLOAT[4096] AS emb FROM r7_templates")
    run("count", "SELECT count(*), count(emb) FROM r7_emb_qwen")
    run("knn_exact_sql", "SELECT template_id, emb <=> ai_embed('Instruct: Given a question about system logs, retrieve log message templates that are evidence for it\nQuery: memory problems', 'qwen3-embedding-8b', 'r7_rwth')::FLOAT[4096] AS d FROM r7_emb_qwen WHERE dataset = 'bgl' ORDER BY d LIMIT 5")
finally:
    run("drop_secret", "DROP SECRET r7_rwth")
    json.dump(log, open("ai_embed_test.json", "w"), indent=1)
