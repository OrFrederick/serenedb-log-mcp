"""Follow-up to crash_discriminate.py: which part of the relevance shape crashes 26.10.1?
I: BM25 selected, ORDER BY s DESC only (single key)
J: BM25 not selected, ORDER BY BM25 DESC, line_no (two keys)
G: control, BM25 selected, ORDER BY s DESC, line_no (crashed 4/8 in crash_discriminate.out)
K: tool shape sort=newest (ORDER BY ts DESC, line_no DESC), no BM25
Usage: python -u crash_relevance.py <rounds>
"""
import sys
import crash_discriminate as cd

W = "FROM r2p_bgl_idx WHERE message @@ websearch_to_tsquery(%s)"
cd.VARIANTS = {
    "I": ("BM25 selected, ORDER BY s DESC (single key), LIMIT 20",
          f"SELECT line_no, ts, node, component, level, message, BM25(r2p_bgl_idx.tableoid) s {W} ORDER BY s DESC LIMIT %s",
          ("error", 20), None),
    "J": ("BM25 not selected, ORDER BY BM25 DESC, line_no (two keys), LIMIT 20",
          f"SELECT line_no, ts, node, component, level, message {W} ORDER BY BM25(r2p_bgl_idx.tableoid) DESC, line_no LIMIT %s",
          ("error", 20), None),
    "G": ("control: BM25 selected, ORDER BY s DESC, line_no, LIMIT 20",
          f"SELECT line_no, ts, node, component, level, message, BM25(r2p_bgl_idx.tableoid) s {W} ORDER BY s DESC, line_no LIMIT %s",
          ("error", 20), None),
    "K": ("tool shape sort=newest, ORDER BY ts DESC, line_no DESC, LIMIT 20",
          f"SELECT line_no, ts, node, component, level, message {W} ORDER BY ts DESC, line_no DESC LIMIT %s",
          ("error", 20), None),
}

if __name__ == "__main__":
    print("# crash_relevance.py (uses crash_discriminate.main with the variants below)", flush=True)
    cd.ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    cd.main()
