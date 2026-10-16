"""Score a per-line cluster-id file (.npy or little-endian .i32) + templates.tsv against a Loghub-2.0 GT template file.
usage: python -I eval_cid.py CID_FILE TEMPLATES_TSV GT_FILE   -> prints JSON metrics (by template string and by cluster id)"""
import sys, os, re, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import all_metrics, load_lines
cf, tf, gf = sys.argv[1:4]
cid = np.load(cf) if cf.endswith('.npy') else np.fromfile(cf, dtype='<i4')
tm = {int(k): v for k, v in (l.rstrip('\n').split('\t', 1) for l in open(tf, encoding='utf-8'))}
norm = lambda s: re.sub(r'<(?:NODE|UUID|REQ|IP|HEX|NUM|\*)>', '<*>', s)
gt = load_lines(gf)
tmpl = [norm(tm.get(int(c), '__ERR__')) for c in cid]
print(json.dumps(dict(n=len(cid), n_clusters=len(tm), by_template_string=all_metrics(gt, tmpl, tmpl), by_cluster_id=all_metrics(gt, cid)), indent=1))
