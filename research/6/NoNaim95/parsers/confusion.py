"""Show the biggest grouping errors: parsed groups that mix GT templates, and GT templates split across parsed groups.
usage: python -I confusion.py GT_FILE CID_NPY TEMPLATES_TSV [top]"""
import sys, numpy as np, pandas as pd, collections
sys.path.insert(0, __file__.rsplit('/',1)[0]); from metrics import load_lines
gt = load_lines(sys.argv[1]); cid = np.load(sys.argv[2]); top = int(sys.argv[4]) if len(sys.argv)>4 else 10
tm = dict(l.rstrip('\n').split('\t',1) for l in open(sys.argv[3]))
df = pd.DataFrame({'g': gt, 'p': cid})
ct = df.groupby(['p','g']).size().reset_index(name='n')
mix = ct.groupby('p').filter(lambda x: len(x) > 1)
print('== parsed clusters mixing >1 GT template (by lines):')
for p, grp in sorted(mix.groupby('p'), key=lambda x: -x[1]['n'].sum())[:top]:
    print(f'  cluster {p} [{grp.n.sum()} lines] tmpl={tm[str(p)][:140]!r}')
    for _, r in grp.sort_values('n', ascending=False).head(4).iterrows(): print(f'      {r.n:>8}  GT {r.g[:130]!r}')
split = ct.groupby('g').filter(lambda x: len(x) > 1)
print('== GT templates split over >1 cluster:')
for g, grp in sorted(split.groupby('g'), key=lambda x: -x[1]['n'].sum())[:top]:
    print(f'  GT [{grp.n.sum()} lines, {len(grp)} clusters] {g[:130]!r}')
    for _, r in grp.sort_values('n', ascending=False).head(3).iterrows(): print(f'      {r.n:>8}  cl {r.p} {tm[str(r.p)][:120]!r}')
