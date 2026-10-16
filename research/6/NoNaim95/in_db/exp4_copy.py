#!/usr/bin/env python3
"""Experiment 4: cost of loading an external parser's (line_no, template_id) mapping (e.g. Drain3 output)
for all 4.7M BGL lines via COPY FROM STDIN, then joining it back.
The mapping is synthesised (template_id = hash-bucket of the masked key) since only the cost matters."""
import io, time, statistics, psycopg

DSN = "host=localhost port=7890 dbname=postgres user=postgres"


def main():
    with psycopg.connect(DSN, autocommit=True) as c:
        # fake parser output: export (line_no, int template id) to a local TSV buffer
        t = time.time()
        rows = c.execute("select line_no, (hash(template_id) % 1000)::int from r6d_bgl_ft").fetchall()
        buf = io.StringIO()
        for ln, tid in rows:
            buf.write(f"{ln}\t{tid}\n")
        data = buf.getvalue().encode()
        print(f"built mapping in client: {len(rows)} rows, {len(data)/1e6:.1f} MB TSV ({time.time()-t:.1f}s, not part of load cost)")
        loads = []
        for i in range(3):
            c.execute("create or replace table r6d_drain_map (line_no bigint, template_id int)")
            t = time.time()
            with c.cursor().copy("copy r6d_drain_map (line_no, template_id) from stdin") as cp:
                for off in range(0, len(data), 1 << 20):
                    cp.write(data[off:off + (1 << 20)])
            loads.append(time.time() - t)
        n = c.execute("select count(*) from r6d_drain_map").fetchone()[0]
        print(f"COPY FROM STDIN text: rows={n} times={['%.2f'%x for x in loads]} median={statistics.median(loads):.2f}s "
              f"-> {n/statistics.median(loads)/1e6:.2f}M rows/s")
        js = []
        for i in range(3):
            t = time.time()
            r = c.execute("""select m.template_id, count(*) n, count(distinct b.node) from bgl b join r6d_drain_map m using(line_no)
                             group by 1 order by n desc limit 3""").fetchall()
            js.append(time.time() - t)
        print(f"join raw bgl x mapping + group by: {['%.2f'%x for x in js]} median={statistics.median(js):.2f}s; top={r[0]}")
        cs = []
        for i in range(3):
            t = time.time()
            c.execute("""create or replace table r6d_bgl_drain as select b.line_no, b.ts, b.node, b.component, m.template_id
                         from bgl b join r6d_drain_map m using(line_no)""")
            cs.append(time.time() - t)
        print(f"CTAS fact table with mapping join: {['%.2f'%x for x in cs]} median={statistics.median(cs):.2f}s")


if __name__ == "__main__":
    main()
