"""Leave-one-factor-out sensitivity of the rank-based Manager priority score (BGL alert categories)."""
import psycopg, pandas as pd, time, pathlib
sql = pathlib.Path(__file__).with_name("priority.sql").read_text().replace("LIMIT 15", "")
t = time.time()
with psycopg.connect("host=localhost port=7890 dbname=postgres user=postgres") as c:
    cur = c.execute(sql)
    df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
print(f"query {time.time()-t:.3f}s, {len(df)} problems")
F = ["pf", "ps", "pp", "pt", "pr"]
df[F] = df[F].astype(float)
base = df.assign(s=df[F].mean(axis=1)).sort_values("s", ascending=False)
top = list(base.problem[:5]); print("all factors top5:", top)
for drop in F:
    keep = [x for x in F if x != drop]
    alt = df.assign(s=df[keep].mean(axis=1)).sort_values("s", ascending=False)
    t5 = list(alt.problem[:5])
    print(f"drop {drop}: top5={t5} overlap={len(set(t5)&set(top))}/5")
print("raw count top5:", list(df.sort_values('entries', ascending=False).problem[:5]))

import itertools
w = {"pf":2,"ps":1,"pp":1,"pt":1,"pr":1}
alt = df.assign(s=sum(df[k]*v for k,v in w.items())/sum(w.values())).sort_values("s", ascending=False)
print("freq weighted x2 top5:", list(alt.problem[:5]))
# lexicographic AP-style: tier by spread>=100 nodes & active in last 30d, then entries
df["tier"] = ((df.nodes>=100).astype(int) + (df.last30>0).astype(int) + (df.active_days>=20).astype(int))
print("tiered (spread>=100, active last30, persist>=20d) then entries:", list(df.sort_values(["tier","entries"],ascending=False).problem[:5]))
