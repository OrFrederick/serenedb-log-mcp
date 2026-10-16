"""Spot-check: does adding line_no = X to an inverted-index query drop the ts range filter? (shared instance, read-only)"""
import psycopg
c=psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres", autocommit=True)
ln=3040656
print("base row:", c.execute("SELECT line_no, ts FROM bgl WHERE line_no=%s",[ln]).fetchall())
for name,sql in [
 ("idx line_no= + july range", f"SELECT line_no, ts FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery('ciod failed') AND ts >= '2005-07-01' AND ts < '2005-08-01' AND line_no = {ln}"),
 ("idx july range only, this line?", f"SELECT count(*) FROM r2_bgl_fts WHERE content @@ websearch_to_tsquery('ciod failed') AND ts >= '2005-07-01' AND ts < '2005-08-01'"),
 ("base table line_no= + july range", f"SELECT line_no, ts FROM bgl WHERE ts >= '2005-07-01' AND ts < '2005-08-01' AND line_no = {ln}"),
]:
    print(name, "->", c.execute(sql).fetchall())
