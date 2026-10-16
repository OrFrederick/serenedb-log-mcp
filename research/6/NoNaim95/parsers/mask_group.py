"""Non-parser baseline: apply the same masking regexes as the tuned Drain3 config, then group by the exact masked string.
(This is what `regexp_replace(...)` + GROUP BY inside SereneDB would compute.)
usage: python -I mask_group.py MSG_FILE [GT_FILE]"""
import sys, os, re, time, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_drain3 import TUNED_MASKS
from metrics import all_metrics, load_lines
R = [(re.compile(p), f'<{m}>') for p, m in TUNED_MASKS]
def mask(s):
    for r, m in R: s = r.sub(m, s)
    return re.sub(r'<(?:NODE|UUID|REQ|IP|NUM)>', '<*>', s)
import numpy as np
t = time.perf_counter(); gid = {}; out = []
with open(sys.argv[1], encoding='utf-8', errors='surrogateescape') as f:
    for l in f: out.append(gid.setdefault(mask(l.rstrip('\n')), len(gid)))
dt = time.perf_counter() - t
out = np.array(out, dtype=np.int32)
st = dict(n=len(out), t_s=round(dt, 2), lines_per_s=round(len(out) / dt), n_groups=len(gid))
if len(sys.argv) > 2:
    tid = {}; g = []; ok = 0; inv = {v: k for k, v in gid.items()}
    with open(sys.argv[2], encoding='utf-8') as f:
        for i, l in enumerate(f):
            l = l.rstrip('\n'); g.append(tid.setdefault(l, len(tid))); ok += inv[out[i]] == l
    st['metrics'] = all_metrics(np.array(g, dtype=np.int32), out); st['metrics']['PA'] = ok / len(out)
c = collections.Counter(out.tolist()); st['top_groups'] = [(inv[k][:120], v) for k, v in c.most_common(5)] if len(sys.argv) > 2 else []
print(json.dumps(st, indent=1))
