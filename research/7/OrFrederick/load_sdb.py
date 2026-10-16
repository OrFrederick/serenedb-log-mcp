"""Create r7_templates (+ inverted index with stemming dictionary) in SereneDB."""
import json, re, psycopg, time
T = json.load(open("templates.json"))
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
c.execute("DROP TABLE IF EXISTS r7_t")
c.execute("DROP TABLE IF EXISTS r7_templates")
c.execute("CREATE TABLE r7_templates (dataset VARCHAR, template_id VARCHAR, body VARCHAR, component VARCHAR, occurrences BIGINT)")
c.execute("CREATE INDEX r7_templates_idx ON r7_templates USING inverted (dataset, template_id, body r7_dict)")
rows = [(ds, t["id"], re.sub(r"\s+", " ", t["template"].replace("<*>", " ")).strip(), t["component"], t["occurrences"]) for ds in T for t in T[ds]]
with c.cursor() as cur:
    cur.executemany("INSERT INTO r7_templates VALUES (%s,%s,%s,%s,%s)", rows)
t0 = time.time(); c.execute("VACUUM (REFRESH_TABLE) r7_templates"); print("refresh s", round(time.time()-t0, 3))
print(c.execute("SELECT dataset, count(*) FROM r7_templates GROUP BY dataset").fetchall())
print(c.execute("SELECT template_id, BM25(r7_templates_idx.tableoid) s FROM r7_templates_idx WHERE dataset = 'bgl' AND body @@ to_tsquery('lustre mount failed') ORDER BY s DESC LIMIT 5").fetchall())
