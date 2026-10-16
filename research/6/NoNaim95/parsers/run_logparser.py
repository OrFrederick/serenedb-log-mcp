"""Run a logparser3 (logpai/logparser v1.0.4, pip `logparser3`) parser over a one-message-per-line file,
with the Loghub-2.0 benchmark parameters for BGL / OpenStack (old_benchmark/*_benchmark.py + Brain/benchmark.py).
usage: python -I run_logparser.py METHOD DATASET(bgl|os) INPUT_MSG_FILE OUTDIR [GT_TEMPLATE_FILE]
The input is fed with log_format '<Content>' (headers already stripped by extract.py)."""
import sys, os, time, json, shutil
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import all_metrics, load_lines
method, ds, inp, out = sys.argv[1:5]; gtf = sys.argv[5] if len(sys.argv) > 5 else None
REX = {'bgl': [r'core\.\d+'], 'os': [r'((\d+\.){3}\d+,?)+', r'/.+?\s', r'\d+']}
os.makedirs(out, exist_ok=True)
name = os.path.basename(inp)
if not os.path.exists(os.path.join(out, name)): os.symlink(os.path.abspath(inp), os.path.join(out, name))
fmt = '<Content>'
if method == 'Drain':
    from logparser.Drain import LogParser
    P = {'bgl': dict(st=0.5, depth=4), 'os': dict(st=0.5, depth=5)}[ds]
    p = LogParser(log_format=fmt, indir=out, outdir=out, rex=REX[ds], **P)
elif method == 'Brain':
    from logparser.Brain import LogParser
    P = {'bgl': 6, 'os': 5}[ds]
    p = LogParser(logname=ds, log_format=fmt, indir=out, outdir=out, threshold=P, delimeter=[], rex=REX[ds])
elif method == 'IPLoM':
    from logparser.IPLoM import LogParser
    P = {'bgl': dict(CT=0.4, lowerBound=0.01), 'os': dict(CT=0.9, lowerBound=0.25)}[ds]
    p = LogParser(log_format=fmt, indir=out, outdir=out, rex=REX[ds], **P)
elif method == 'Spell':
    from logparser.Spell import LogParser
    P = {'bgl': 0.75, 'os': 0.9}[ds]
    p = LogParser(indir=out, outdir=out, log_format=fmt, tau=P, rex=REX[ds])
elif method == 'AEL':
    from logparser.AEL import LogParser
    P = {'bgl': dict(minEventCount=2, merge_percent=0.5), 'os': dict(minEventCount=6, merge_percent=0.5)}[ds]
    p = LogParser(indir=out, outdir=out, log_format=fmt, rex=REX[ds], **P)
t0 = time.perf_counter(); p.parse(name); t = time.perf_counter() - t0
res = pd.read_csv(os.path.join(out, name + '_structured.csv'), dtype=str, keep_default_na=False)
n_in = sum(1 for _ in open(inp, encoding='utf-8', errors='surrogateescape'))
st = dict(method=method, dataset=ds, input=inp, n_in=n_in, n_out=len(res), t_parse_s=round(t, 2),
          lines_per_s=round(n_in / t), n_templates=int(res['EventTemplate'].nunique()), n_eventids=int(res['EventId'].nunique()))
if gtf and len(res) == n_in:
    gt = load_lines(gtf)
    st['metrics'] = all_metrics(gt, res['EventTemplate'].tolist(), res['EventTemplate'].tolist())
print(json.dumps(st, indent=1)); json.dump(st, open(os.path.join(out, 'stats.json'), 'w'), indent=1)
# keep only small outputs
os.remove(os.path.join(out, name + '_structured.csv')) if os.path.getsize(os.path.join(out, name + '_structured.csv')) > 5e6 and os.environ.get('KEEP') is None else None
