"""Crash check of the two search_logs SQL shapes we propose to ship (native 26.10.1), with a bound time filter.
L: sort=relevance, single sort key, filter as ts_between(...)::score(NULL)
M: sort=time (default), plain ts range filter
Usage: python -u crash_shipshape.py <rounds>
"""
import sys
import crash_discriminate as cd

W = "FROM r2p_bgl_idx WHERE message @@ websearch_to_tsquery(%s)"
cd.VARIANTS = {
    "L": ("sort=relevance: BM25 selected, ORDER BY s DESC (single key), ts_between(%s,%s)::score(NULL), LIMIT 20",
          f"SELECT line_no, ts, node, component, level, message, BM25(r2p_bgl_idx.tableoid) s {W} "
          "AND ts @@ ts_between(%s::timestamp, %s::timestamp, true, false)::score(NULL) ORDER BY s DESC LIMIT %s",
          ("error", "2005-06-01", "2005-07-01", 20), None),
    "M": ("sort=time: plain ts range, ORDER BY ts, line_no, LIMIT 20",
          f"SELECT line_no, ts, node, component, level, message {W} AND ts >= %s::timestamp AND ts < %s::timestamp "
          "ORDER BY ts, line_no LIMIT %s",
          ("error", "2005-06-01", "2005-07-01", 20), None),
}

if __name__ == "__main__":
    print("# crash_shipshape.py (uses crash_discriminate.main with the variants below)", flush=True)
    cd.ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    cd.main()
