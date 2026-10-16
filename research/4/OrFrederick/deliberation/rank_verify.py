"""Deliberation #4 (OrFrederick): re-derive Manager rankings A (ours), B (NoNaim95 b), C (A with Incidents)
on the shared bgl table, with OUR feature definitions, plus the spread/eff-sources facts disputed.
Incident = per alert category, merged across all nodes, new Incident when gap >= 300 s (#1 definition)."""
import time, itertools, math, psycopg, numpy as np, pandas as pd
from scipy.stats import kendalltau
END = "TIMESTAMP '2006-01-04 08:00:05'"
SQL = f"""
WITH a AS (
  SELECT alert_label AS cat, ts, node, midplane,
         split_part(node,'-',1)||'-'||split_part(node,'-',2)||'-'||split_part(node,'-',3) AS nc,
         (epoch(ts) - epoch(TIMESTAMP '2005-06-03 00:00:00'))::bigint / 604800 AS wk,
         lag(ts) OVER (PARTITION BY alert_label ORDER BY ts) AS prev
  FROM bgl WHERE alert_label <> '-'),
pn AS (SELECT cat, node, count(*) AS n FROM a GROUP BY 1,2),
pnt AS (SELECT cat, sum(n)::bigint AS tot, max(n) AS top_n, count(*) AS nodes FROM pn GROUP BY 1),
eff AS (SELECT pn.cat, 1.0/sum((pn.n::double precision/pnt.tot)^2) AS eff
        FROM pn JOIN pnt USING (cat) GROUP BY 1)
SELECT a.cat, count(*) AS lines,
  count(*) FILTER (WHERE prev IS NULL OR epoch(ts)-epoch(prev) >= 300) AS incidents,
  count(DISTINCT nc || date_trunc('hour', ts)::text) AS nc_hours,
  count(DISTINCT CASE WHEN ts >= {END} - INTERVAL 28 DAY THEN nc || date_trunc('hour', ts)::text END) AS nc_hours_last28,
  count(*) FILTER (WHERE ts >= {END} - INTERVAL 28 DAY AND (prev IS NULL OR epoch(ts)-epoch(prev) >= 300)) AS inc_last28,
  count(*) FILTER (WHERE ts >= {END} - INTERVAL 28 DAY) AS lines_last28,
  count(DISTINCT midplane) AS midplanes,
  count(DISTINCT wk) AS active_weeks,
  count(DISTINCT date_trunc('day', ts)) AS active_days,
  (epoch({END}) - epoch(max(ts)))::bigint / 86400 AS days_since_last,
  max(pnt.nodes) AS nodes, max(pnt.top_n)::double precision / count(*) AS top_node_share, max(eff.eff) AS eff_sources
FROM a JOIN pnt USING (cat) JOIN eff USING (cat)
GROUP BY a.cat"""
t0 = time.time()
with psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres") as c:
    cur = c.execute(SQL); df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description]).set_index('cat')
print(f"features query: {time.time()-t0:.2f} s, {len(df)} categories, {df.lines.sum()} alert lines")
df = df.astype({k: float for k in df.columns})
SPAN = 214.7
df['ratio_nch'] = df.nc_hours_last28 / (df.nc_hours * 28 / SPAN)
df['ratio_inc'] = df.inc_last28 / (df.incidents * 28 / SPAN)
def prmean(cols):  # equal-weight mean of percentile ranks (pandas, ties = average)
    return pd.DataFrame({k: s.rank(pct=True) for k, s in cols.items()}).mean(axis=1)
A = prmean(dict(f=df.nc_hours, s=df.midplanes, p=df.active_weeks, t=df.ratio_nch, r=-df.days_since_last))
C = prmean(dict(f=df.incidents, s=df.midplanes, p=df.active_weeks, t=df.ratio_inc, r=-df.days_since_last))
C2 = prmean(dict(f=df.incidents, s=df.midplanes, p=df.active_weeks, t=df.ratio_nch, r=-df.days_since_last))
mm = lambda v: (v - v.min()) / (v.max() - v.min())
ratio_l = df.lines_last28 / (df.lines * 28 / SPAN)
T = np.where(df.lines >= 30, np.clip(np.log2(np.maximum(ratio_l, 1e-3)), -3, 3), 0)
B = (.25*mm(np.log10(1+df.lines/SPAN)) + .25*mm(np.log(df.eff_sources)) + .15*mm(df.active_days/SPAN)
     + .15*mm(pd.Series(T, index=df.index)) + .10*mm(1-df.days_since_last/SPAN))
S = pd.DataFrame(dict(L=df.lines, A=A, B=B, C=C, C2=C2))
R = S.rank(ascending=False, method='min').astype(int)
print("\nTop 8 per formula (L=raw lines, A=ours, B=NoNaim95 (b), C=A with Incidents incl. Incident-based trend, C2=C with our nc-hour trend)")
for k in range(8):
    print(f"{k+1:2} " + " | ".join(f"{S[col].sort_values(ascending=False).index[k]:9}" for col in S))
show = ['lines','incidents','nc_hours','nodes','top_node_share','eff_sources','midplanes','active_weeks','ratio_nch','ratio_inc','days_since_last']
print("\nKey categories (ranks L/A/B/C/C2):")
for cat in ['KERNDTLB','KERNSTOR','APPSEV','KERNMNTF','KERNMC','KERNRTSP','KERNSOCK','APPOUT']:
    r = df.loc[cat]
    print(f"{cat:9} " + " ".join(f"{k}={r[k]:.3g}" for k in show) + f"  ranks {'/'.join(str(R.loc[cat,c]) for c in S)}")
print("\nKendall tau:", {f"{x}-{y}": round(kendalltau(S[x], S[y])[0], 3) for x, y in itertools.combinations(S.columns, 2)})
print("Top-5 sets equal A vs C:", set(R.A[R.A<=5].index) == set(R.C[R.C<=5].index), sorted(R.C[R.C<=5].index))
rng = np.random.default_rng(4)
for name, cols in [('C', dict(f=df.incidents, s=df.midplanes, p=df.active_weeks, t=df.ratio_inc, r=-df.days_since_last))]:
    P = pd.DataFrame({k: s.rank(pct=True) for k, s in cols.items()}); top5 = pd.Series(0, index=df.index)
    for _ in range(2000):
        w = rng.dirichlet(np.ones(5)); s = (P*w).sum(axis=1); top5[s.rank(ascending=False) <= 5] += 1
    print(f"{name} in-top-5 % over 2000 Dirichlet weightings:", (top5/20).sort_values(ascending=False).head(7).round(1).to_dict())
