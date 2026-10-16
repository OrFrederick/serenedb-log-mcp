"""Is Loghub-2.0 BGL_full.log an ordered subsequence of raw BGL.log? Characterise removed lines."""
import collections, re, time
LH2 = "/Users/deni/hackathon/data/lh2/BGL/BGL_full.log"; RAW = "/Users/deni/hackathon/data/raw/BGL.log"
t0 = time.time()
fl = open(LH2, encoding="utf-8", errors="replace"); cur = fl.readline()
in_order = 0; removed = collections.Counter(); removed_tag = collections.Counter(); ex = {}
mask = lambda s: re.sub(r"\d+", "#", s)[:90]
for line in open(RAW, encoding="utf-8", errors="replace"):
    if cur and line.rstrip("\n") == cur.rstrip("\n"):
        in_order += 1; cur = fl.readline()
    else:
        p = line.rstrip("\n").split(" ", 9)
        tag = p[0]; msg = p[9] if len(p) > 9 else ""
        k = (p[7] if len(p) > 7 else "?", mask(msg)); removed[k] += 1; removed_tag[tag] += 1
print(f"ordered-subsequence matches={in_order} leftover lh2 unmatched={'yes' if cur else 'no'} t={time.time()-t0:.1f}s")
print("removed by alert tag:", removed_tag.most_common(12))
print("top removed (component, masked message):")
for k, c in removed.most_common(25): print(c, k)
print("distinct removed masked messages:", len(removed))
