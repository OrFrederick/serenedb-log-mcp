# Re-run of NoNaim95 research/4 deliberation/rank_compare.py (commit 888c02c) on our 26.09.1.3 dev tables; only column name + DSN changed.
"""Deliberation #4: compare Manager rankings on BGL alert categories.
Features per category (all measured):
  inc   = Incidents (#1 definition: per-category chained gap 300 s, across nodes)
  ncu   = node-card-hours (OrFrederick's frequency unit)
  lines = raw Log Entries
  mp    = distinct midplanes; eff = effective sources 1/sum(share^2)
  wk    = active weeks; ratio = last-28-day rate / whole-period rate; rec = days since last seen (corpus end)
Formulas: A = OrFrederick (pct-rank mean of ncu, mp, wk, ratio, -rec)
          B = NoNaim95 (b) (weighted minmax: lines/day, ln eff, persistence, trend, recency)
          C = proposed merge (pct-rank mean of inc, mp, wk, ratio, -rec)
"""
import psycopg, math, itertools, random
SQL = """
with a as (select alert_label c, node, ts, left(node,6) mp, left(node,9) nc,
             lag(ts) over (partition by alert_label order by ts) p from bgl where alert_label<>'-'),
inc as (select c, count(*) filter (where p is null or epoch(ts)-epoch(p) >= 300) inc from a group by 1),
per_node as (select c, node, count(*) n from a group by 1,2),
eff as (select c, 1.0/sum((n::double/t)^2) eff from (select *, sum(n) over (partition by c) t from per_node) group by 1),
f as (select c, count(*) lines, count(distinct mp) filter (where mp like 'R__-M_') mp,
        count(distinct nc || date_trunc('hour',ts)::varchar) ncu,
        count(distinct date_trunc('week',ts)) wk, count(distinct date_trunc('day',ts)) days,
        count(*) filter (where ts > timestamp '2006-01-04 08:00:05' - interval 28 day) last28,
        date_diff('day', max(ts), timestamp '2006-01-04 08:00:05') rec from a group by 1)
select f.*, inc.inc, eff.eff from f join inc using (c) join eff using (c)"""
with psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres password=postgres") as conn:
    cur = conn.execute(SQL); cols = [d.name for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
SPAN = 214.7
for r in rows:
    r['ratio'] = (r['last28']/28) / (r['lines']/SPAN)
def pct(vals):
    s = sorted(vals); n = len(s)
    return [ (sorted(s).index(v))/(n-1) for v in vals]  # percent_rank (min rank for ties)
def pr_mean(keys, signs):
    cols = [pct([s*r[k] for r in rows]) for k, s in zip(keys, signs)]
    return [sum(c[i] for c in cols)/len(cols) for i in range(len(rows))]
def minmax(v):
    lo, hi = min(v), max(v); return [(x-lo)/(hi-lo) if hi > lo else 0 for x in v]
A = pr_mean(['ncu','mp','wk','ratio','rec'], [1,1,1,1,-1])
C = pr_mean(['inc','mp','wk','ratio','rec'], [1,1,1,1,-1])
F = minmax([math.log10(1+r['lines']/SPAN) for r in rows]); S = minmax([math.log(r['eff']) for r in rows])
P = minmax([r['days']/SPAN for r in rows]); T = minmax([max(-3,min(3,math.log2(max(r['ratio'],1e-3)))) if r['lines']>=30 else 0 for r in rows])
R = minmax([1-r['rec']/SPAN for r in rows])
B = [0.25*F[i]+0.25*S[i]+0.15*P[i]+0.15*T[i]+0.10*R[i] for i in range(len(rows))]
L = [r['lines'] for r in rows]
def order(score): return [rows[i]['c'] for i in sorted(range(len(rows)), key=lambda i: -score[i])]
def rank_of(score, c): return order(score).index(c)+1
def tau(s1, s2):
    n=len(s1); c=d=0
    for i,j in itertools.combinations(range(n),2):
        x=(s1[i]-s1[j])*(s2[i]-s2[j]); c+= x>0; d+= x<0
    return (c-d)/(n*(n-1)/2)
print("top10  lines | A(OrFrederick) | B(NoNaim95 b) | C(merge: Incidents)")
for k,(l,a,b,c) in enumerate(zip(order(L),order(A),order(B),order(C)),1):
    if k<=10: print(f"{k:2} {l:9} | {a:9} | {b:9} | {c:9}")
for cat in ['KERNDTLB','KERNSTOR','KERNMC','KERNRTSP','APPSEV','KERNMNTF']:
    r = next(x for x in rows if x['c']==cat)
    print(f"{cat}: lines={r['lines']} inc={r['inc']} ncu={r['ncu']} mp={r['mp']} eff={r['eff']:.0f} wk={r['wk']} ratio={r['ratio']:.2f} rec={r['rec']}  ranks L/A/B/C = {rank_of(L,cat)}/{rank_of(A,cat)}/{rank_of(B,cat)}/{rank_of(C,cat)}")
print(f"tau A-B {tau(A,B):.3f}  A-C {tau(A,C):.3f}  B-C {tau(B,C):.3f}  L-A {tau(L,A):.3f} L-C {tau(L,C):.3f}")
# robustness of C under random Dirichlet weights
random.seed(1); keys=['inc','mp','wk','ratio','rec']; signs=[1,1,1,1,-1]
cols=[pct([s*r[k] for r in rows]) for k,s in zip(keys,signs)]; top5={}
for _ in range(2000):
    w=[random.expovariate(1) for _ in keys]; sw=sum(w)
    sc=[sum(w[j]*cols[j][i] for j in range(5))/sw for i in range(len(rows))]
    for c in order(sc)[:5]: top5[c]=top5.get(c,0)+1
print("C top5 frequency under 2000 Dirichlet weightings:", sorted(((v/20,k) for k,v in top5.items()), reverse=True)[:7])
