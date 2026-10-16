"""Template mining INSIDE SereneDB: generic regexp_replace masking -> template_id,
plus storage-design and GROUP BY/trend timings. Creates only r6_* tables.

Usage: python -I sql_templates.py <data_dir> [--keep]
Prints timings (and the exact SQL) as JSON lines; compares SQL template ids with the
Python implementation (<data_dir>/r6/<ds>_mask.tsv from template_lab.py).
"""
import json
import sys
import time
from pathlib import Path

import psycopg

DATA = Path(sys.argv[1])
KEEP = "--keep" in sys.argv
sys.argv = [sys.argv[0], str(DATA), "mask", "bgl"]  # template_lab reads argv at import
sys.path.insert(0, str(Path(__file__).parent))
import template_lab as tl  # noqa: E402

DSN = "host=localhost port=7890 dbname=postgres user=postgres password=postgres"
MASK = tl.sql_mask_expr("content")
TPL_ID = f"left(md5(regexp_replace({MASK}, '<[A-Z]+>|<\\*>', '<*>', 'g')), 16)"


def q(c, name, sql, fetch=True, reps=1):
    best = None
    rows = None
    for _ in range(reps):
        t = time.perf_counter()
        cur = c.execute(sql)
        rows = cur.fetchall() if fetch and cur.description else None
        dt = time.perf_counter() - t
        best = dt if best is None else min(best, dt)
    out = {"step": name, "seconds": round(best, 3), "reps": reps,
           "sql": " ".join(sql.split())[:600]}
    if rows is not None:
        out["rows"] = len(rows)
        out["sample"] = [list(map(str, r)) for r in rows[:5]]
    print(json.dumps(out), flush=True)
    return rows


def main():
    c = psycopg.connect(DSN, autocommit=True)
    for ds in ["openstack", "bgl"]:
        n = c.execute(f"select count(*) from {ds}").fetchone()[0]
        # 1) pure compute: distinct templates without materialising
        q(c, f"{ds}: count distinct template_id (compute only, {n} rows)",
          f"SELECT count(DISTINCT {TPL_ID}) FROM {ds}")
        # 2) materialise line_no -> template_id (+ template text) side table
        c.execute(f"DROP TABLE IF EXISTS r6_{ds}_tid")
        q(c, f"{ds}: CTAS r6_{ds}_tid(line_no, template_id, template)",
          f"CREATE TABLE r6_{ds}_tid AS SELECT line_no, {TPL_ID} AS template_id, {MASK} AS template "
          f"FROM {ds}", fetch=False)
        # 3) templates dimension table
        c.execute(f"DROP TABLE IF EXISTS r6_{ds}_templates")
        q(c, f"{ds}: CTAS r6_{ds}_templates",
          f"CREATE TABLE r6_{ds}_templates AS SELECT template_id, min(template) AS template, "
          f"count(*) AS n_lines, min(line_no) AS first_line_no FROM r6_{ds}_tid GROUP BY template_id",
          fetch=False)
        q(c, f"{ds}: #templates", f"SELECT count(*), sum(n_lines) FROM r6_{ds}_templates")
        # 4) parity with Python implementation
        py = {}
        for line in open(DATA / f"r6/{ds}_mask.tsv"):
            ln, tid, _ = line.split("\t", 2)
            py[int(ln)] = tid
        t = time.perf_counter()
        diff = 0
        examples = []
        with c.cursor() as cur:
            with cur.copy(f"COPY (SELECT line_no, template_id FROM r6_{ds}_tid) TO STDOUT") as cp:
                for row in cp.rows():
                    ln, tid = int(row[0]), row[1]
                    if py.get(ln) != tid:
                        diff += 1
                        if len(examples) < 5:
                            examples.append(ln)
        ex = []
        for ln in examples:
            r = c.execute(f"SELECT content, (SELECT template FROM r6_{ds}_tid t WHERE t.line_no={ln}) "
                          f"FROM {ds} WHERE line_no={ln}").fetchone()
            ex.append([ln, r[0][:160], r[1][:160], tl.mask(r[0])[:160]])
        print(json.dumps({"step": f"{ds}: python vs sql template_id parity", "lines": len(py),
                          "mismatches": diff, "examples(line,content,sql,python)": ex,
                          "seconds": round(time.perf_counter() - t, 1)}), flush=True)

    # 5) storage variants for GROUP BY / trends on BGL
    c.execute("DROP TABLE IF EXISTS r6_bgl_wide")
    q(c, "bgl: CTAS r6_bgl_wide = bgl.* + template_id (denormalised copy)",
      "CREATE TABLE r6_bgl_wide AS SELECT b.*, t.template_id FROM bgl b JOIN r6_bgl_tid t USING (line_no)",
      fetch=False)
    top = ("SELECT {tid}, count(*) AS n, count(DISTINCT {node}) AS nodes FROM {src} "
           "WHERE {lvl} IN ('FATAL','FAILURE','SEVERE','ERROR') GROUP BY 1 ORDER BY n DESC LIMIT 10")
    trend = ("SELECT {tid}, date_trunc('day', {ts}) AS day, count(*) AS n FROM {src} "
             "WHERE {lvl} IN ('FATAL','FAILURE','SEVERE','ERROR') AND {ts} >= TIMESTAMP '2005-06-01' "
             "AND {ts} < TIMESTAMP '2006-01-01' GROUP BY 1, 2 ORDER BY 1, 2")
    trend_comp = ("SELECT {comp}, {tid}, date_trunc('week', {ts}) AS wk, count(*) AS n FROM {src} "
                  "GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 50")
    variants = {
        "join side table": dict(src="bgl b JOIN r6_bgl_tid t ON b.line_no = t.line_no", tid="t.template_id",
                                node="b.node", lvl="b.level", ts="b.ts", comp="b.component"),
        "denormalised copy": dict(src="r6_bgl_wide", tid="template_id", node="node", lvl="level", ts="ts",
                                  comp="component"),
        "mask at query time": dict(src="bgl", tid=TPL_ID, node="node", lvl="level", ts="ts", comp="component"),
    }
    for vname, v in variants.items():
        for qname, tmpl in [("top10 error templates", top), ("daily trend per template", trend),
                            ("weekly component x template", trend_comp)]:
            q(c, f"bgl {qname} [{vname}]", tmpl.format(**v), reps=3 if vname != "mask at query time" else 1)

    # 6) can a post-load step add the column in place? (on a scratch copy, never on bgl)
    c.execute("DROP TABLE IF EXISTS r6_alter_test")
    c.execute("CREATE TABLE r6_alter_test AS SELECT line_no, content FROM openstack")
    for name, sql in [("ALTER TABLE ADD COLUMN", "ALTER TABLE r6_alter_test ADD COLUMN template_id TEXT"),
                      ("UPDATE ... SET template_id = mask(content)",
                       f"UPDATE r6_alter_test SET template_id = {TPL_ID}"),
                      ("UPDATE ... FROM side table",
                       "UPDATE r6_alter_test a SET template_id = t.template_id FROM r6_openstack_tid t "
                       "WHERE a.line_no = t.line_no")]:
        try:
            q(c, f"openstack scratch: {name}", sql, fetch=False)
        except Exception as e:  # record unsupported features as facts
            print(json.dumps({"step": f"openstack scratch: {name}", "error": str(e)[:300]}), flush=True)
    try:
        q(c, "openstack scratch: check", "SELECT count(*), count(template_id) FROM r6_alter_test")
    except Exception as e:
        print(json.dumps({"step": "check", "error": str(e)[:300]}))
    if not KEEP:
        c.execute("DROP TABLE IF EXISTS r6_alter_test")


if __name__ == "__main__":
    main()
