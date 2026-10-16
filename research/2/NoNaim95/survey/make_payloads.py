"""Render the same 50 BGL search hits in 4 formats to measure Claude token cost per format."""
import json, psycopg, csv, io, sys
c = psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres", autocommit=True)
rows = c.execute("SELECT line_no, ts, node, component, level, message FROM r2p_bgl_idx WHERE message @@ %s ORDER BY line_no LIMIT 50", ("error",)).fetchall()
cols = ["line_no", "ts", "node", "component", "level", "message"]
recs = [dict(zip(cols, [r[0], r[1].isoformat(sep=' '), *r[2:]])) for r in rows]
out = {
 "json_objects_indented": json.dumps(recs, indent=2),
 "json_objects_compact": json.dumps(recs, separators=(",", ":")),
 "json_columns_rows": json.dumps({"columns": cols, "rows": [list(d.values()) for d in recs]}, separators=(",", ":")),
}
buf = io.StringIO(); w = csv.writer(buf, delimiter="\t", lineterminator="\n"); w.writerow(cols); [w.writerow(d.values()) for d in recs]
out["tsv"] = buf.getvalue()
md = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n" + "".join("| " + " | ".join(str(v) for v in d.values()) + " |\n" for d in recs)
out["markdown_table"] = md
for k, v in out.items():
    open(f"payload_{k}.txt", "w").write(v); print(k, len(v), "chars")
