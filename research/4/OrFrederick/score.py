"""Manager priority score from measured features only; sensitivity to weights."""
import io, sys, itertools, numpy as np, pandas as pd
from scipy.stats import kendalltau
raw = open(sys.argv[1]).read().split('\n-- ')[0]
df = pd.read_csv(io.StringIO(raw), sep=r' \| ', engine='python').set_index('cat')
SPAN_D = 214.7
df['recent_ratio'] = df.nc_hours_last28d / (df.nc_hours * 28 / SPAN_D)   # >1 = more active in last 28 d than its average
feat = {  # all higher = more attention
  'frequency':   df.nc_hours,          # node-card-hours, de-duplicates bursts
  'spread':      df.midplanes,         # of 128 midplanes
  'persistence': df.active_weeks,      # of 31 weeks
  'trend':       df.recent_ratio,
  'recency':     -df.days_since_last,
}
P = pd.DataFrame({k: v.rank(pct=True) for k, v in feat.items()})
W0 = dict(frequency=.2, spread=.2, persistence=.2, trend=.2, recency=.2)
def score(w): return sum(P[k]*w[k] for k in w) / sum(w.values())
df['score'] = score(W0); df['rank'] = df.score.rank(ascending=False, method='min').astype(int)
df['rank_by_lines'] = df.lines.rank(ascending=False, method='min').astype(int)
df['line_share_%'] = (100*df.lines/df.lines.sum()).round(1)
df['cum_line_share_%'] = df.sort_values('lines', ascending=False)['line_share_%'].cumsum().reindex(df.index).round(1)
cols = ['rank','score','rank_by_lines','lines','line_share_%','cum_line_share_%','nc_hours','midplanes','active_weeks','recent_ratio','days_since_last']
out = df.sort_values('score', ascending=False)[cols]
pd.set_option('display.width', 200)
print(out.head(12).round(3).to_string())
print('\nKendall tau(score rank, raw line rank) over all cats: %.2f' % kendalltau(df['rank'], df.rank_by_lines)[0])
# Sensitivity 1: drop-one-feature
base_top = list(out.index[:5])
print('\nDefault top5:', base_top)
for k in W0:
    w = dict(W0); w[k] = 0
    s = score(w).sort_values(ascending=False)
    print(f'without {k:12s} top5:', list(s.index[:5]), ' tau vs default %.2f' % kendalltau(s.reindex(df.index), df.score)[0])
# Sensitivity 2: 2000 random Dirichlet weightings
rng = np.random.default_rng(4); top5 = pd.Series(0, index=df.index); top1 = pd.Series(0, index=df.index); taus=[]
for _ in range(2000):
    w = dict(zip(W0, rng.dirichlet(np.ones(5))))
    s = score(w); r = s.rank(ascending=False)
    top5[r[r <= 5].index] += 1; top1[r.idxmin()] += 1
    taus.append(kendalltau(s, df.score)[0])
print('\nRandom weights (n=2000): share of weightings where category is in top 5 / is #1')
print(pd.DataFrame({'in_top5_%': (top5/20).round(1), 'is_top1_%': (top1/20).round(1)}).sort_values('in_top5_%', ascending=False).head(10).to_string())
print('Kendall tau vs default: median %.2f, 5th pct %.2f' % (np.median(taus), np.percentile(taus, 5)))
