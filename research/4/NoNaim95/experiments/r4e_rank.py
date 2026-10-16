"""Step 2: candidate Manager prioritisation formulas over measured factors (r4e_factors), rankings, Kendall tau, weight sensitivity."""
import numpy as np, pandas as pd, sys
from scipy.stats import kendalltau
from r4e_common import *
c = conn()
cols, rows, _ = run(c, "SELECT * FROM r4e_factors", quiet=True)
df = pd.DataFrame(rows, columns=cols)
bc, br, _ = run(c, "SELECT dataset, kind, gkey, bursts FROM r4e_bursts", quiet=True)
df = df.merge(pd.DataFrame(br, columns=bc), on=["dataset", "kind", "gkey"], how="left")
df["bursts"] = df.bursts.astype(float)
for k in ["n","n_recent","n_base","u_recent","u_base","active_days","active_hours","nodes","racks","comps","teams","sources","active_minutes"]:
    df[k] = df[k].astype(float)
COVER = {"BGL": dict(days=215, hours=3618, span_days=214.7), "OpenStack": dict(days=3, hours=32, span_days=2.68)}
df["cov_days"] = df.dataset.map(lambda d: COVER[d]["days"])
df["cov_hours"] = df.dataset.map(lambda d: COVER[d]["hours"])
df["span_days"] = df.dataset.map(lambda d: COVER[d]["span_days"])
# --- measured, unit-consistent factors ---
df["rate_per_cov_day"] = df.n / (df.cov_hours / 24.0)              # entries per covered day of its Dataset
df["persistence"] = np.where(df.dataset == "BGL", df.active_days / df.cov_days, df.active_hours / df.cov_hours)
df["log2_trend"] = np.clip(np.log2(df.trend_ratio.astype(float)), -3, 3)
df["recency_frac"] = df.recency_days.astype(float) / df.span_days   # 0 = seen at Dataset end
# Evidence floor: a trend from <30 entries is not measured, it is noise -> neutral (ratio 1)
MIN_TREND_N = 30
df.loc[(df.n_recent + df.n_base) < MIN_TREND_N, "log2_trend"] = 0.0
df["label"] = np.where(df.kind == "alert_tag", df.gkey, df.dataset.str[:2] + ":" + df.gkey.str[:48])

def mm(x):
    x = np.asarray(x, float); r = x.max() - x.min()
    return (x - x.min()) / r if r > 0 else np.zeros_like(x)

def factors01(d):
    return pd.DataFrame({
        "FB": mm(np.log10(1 + d.bursts / (d.cov_hours / 24.0))),  # frequency of Bursts (episodes), not entries
        "F": mm(np.log10(1 + d.rate_per_cov_day)),          # frequency (log)
        "S": mm(np.log(d.eff_sources.astype(float))),       # spread = log effective sources (1/HHI)
        "P": mm(d.persistence),                            # persistence
        "T": (d.log2_trend + 3) / 6.0,                     # trend, clipped log2 ratio
        "R": 1 - np.clip(d.recency_frac, 0, 1),            # recency
        "Tm": mm(d.teams),                                 # placeholder Team count
    }, index=d.index)

W0 = dict(F=.25, S=.25, P=.15, T=.15, R=.10, Tm=.10)
def f_raw(d): return d.n.values
def f_comp(d, W=W0):
    X = factors01(d); return sum(W[k] * X[k].values for k in W)
def f_borda(d):
    X = factors01(d)
    return sum(X[k].rank(method="average").values for k in ["F","S","P","T","R"])   # Tm omitted: constant
def decile(x):  # 1..10 via percentile rank
    return np.ceil(pd.Series(x).rank(pct=True).values * 10).clip(1, 10)
def f_fmea(d):
    X = factors01(d)
    O = decile(X.F); Sp = decile(X.S); Tr = 1 + np.round(X["T"].values * 9)
    return O * Sp * Tr

def f_comp_bursts(d, W=W0):
    X = factors01(d); X["F"] = X["FB"]; return sum(W[k] * X[k].values for k in W)
FORMULAS = {"a_raw": f_raw, "b_composite": f_comp, "c_borda": f_borda, "d_fmea": f_fmea, "e_composite_bursts": f_comp_bursts}

def analyse(d, title, out):
    d = d.copy()
    for k, f in FORMULAS.items(): d[k] = f(d)
    X = factors01(d)
    out.append(f"\n### {title} ({len(d)} groups)\n")
    for k in FORMULAS:
        d[k + "_rank"] = d[k].rank(ascending=False, method="min").astype(int)
        top = d.sort_values(k, ascending=False).head(10)
        out.append(f"\n**Top 10 by {k}**\n")
        out.append(md_table(["rank","group","n","bursts","eff_src","nodes","racks","persist","trend","rec_d","burst","score"],
            [(int(r[k+"_rank"]), r.label, int(r.n), int(r.bursts), round(r.eff_sources,1), int(r.nodes), int(r.racks), round(r.persistence,3),
              round(float(r.trend_ratio),3), round(float(r.recency_days),1), round(float(r.burstiness),3), round(float(r[k]),3)) for _, r in top.iterrows()]))
    out.append("\n**Kendall tau-b between formula rankings (all groups in universe)**\n")
    ks = list(FORMULAS); M = []
    for a in ks:
        M.append([a] + [round(kendalltau(d[a], d[b]).statistic, 3) for b in ks])
    out.append(md_table(["", *ks], M))
    # top-10 overlap
    tops = {k: set(d.sort_values(k, ascending=False).head(10).index) for k in ks}
    out.append("\n**Top-10 overlap (count of shared groups)**\n")
    out.append(md_table(["", *ks], [[a] + [len(tops[a] & tops[b]) for b in ks] for a in ks]))
    # rank displacement for most-concentrated groups (single-source spam)
    spam = d[(d.eff_sources < 2) & (d.n >= 1000)].sort_values("n", ascending=False)
    out.append("\n**Single-source groups (eff_sources<2, n>=1000): rank under each formula**\n")
    out.append(md_table(["group","n","top_src_share","eff_src", *[k+"_rank" for k in ks]],
        [(r.label, int(r.n), round(r.top_src_share,3), round(r.eff_sources,2), *[int(r[k+"_rank"]) for k in ks]) for _, r in spam.head(10).iterrows()]))
    # weight sensitivity for composite: Dirichlet around W0 (concentration 20) and uniform Dirichlet
    rng = np.random.default_rng(4)
    base_top = list(d.sort_values("b_composite", ascending=False).head(10).index)
    keys = list(W0)
    for conc_name, alpha in [("near default (Dirichlet 20*W0)", np.array([W0[k] for k in keys]) * 20), ("any weights (Dirichlet 1)", np.ones(len(keys)))]:
        taus, overl, cnt = [], [], pd.Series(0, index=d.index)
        for _ in range(1000):
            w = dict(zip(keys, rng.dirichlet(alpha)))
            s = f_comp(d, w)
            taus.append(kendalltau(s, d.b_composite).statistic)
            t10 = d.index[np.argsort(-s)[:10]]
            overl.append(len(set(t10) & set(base_top))); cnt[t10] += 1
        out.append(f"\n**Composite weight sensitivity, {conc_name}, 1000 draws:** Kendall tau vs default median {np.median(taus):.3f} (p5 {np.percentile(taus,5):.3f}); top-10 overlap with default median {np.median(overl):.0f}/10 (p5 {np.percentile(overl,5):.0f}). Share of draws in top-10 for default top-10:\n")
        out.append(md_table(["group","in_top10_share"], [(d.loc[i,"label"], round(cnt[i]/1000,3)) for i in base_top]))
    # leave-one-factor-out
    rows_ = []
    for k in ["F","S","P","T","R"]:
        w = dict(W0); w[k] = 0; tot = sum(w.values()); w = {a: b/tot for a, b in w.items()}
        s = f_comp(d, w); t10 = set(d.index[np.argsort(-s)[:10]])
        rows_.append((f"drop {k}", round(kendalltau(s, d.b_composite).statistic, 3), len(t10 & set(base_top))))
    out.append("\n**Leave-one-factor-out (composite)**\n")
    out.append(md_table(["variant","tau_vs_default","top10_overlap"], rows_))
    return d

out = ["# r4e_rank output (generated by r4e_rank.py)\n",
       "Factor definitions: F=log10(1+entries per covered day), S=ln(effective sources=1/HHI over node/source), P=active days/covered days (BGL) or active hours/covered hours (OpenStack), T=clip(log2 trend_ratio,±3), R=1-recency/span, Tm=placeholder Team count (constant 1 -> 0). Trend set neutral when n_recent+n_base < 30 (evidence floor). Default composite weights: " + str(W0)]
UA = df[(df.kind == "alert_tag") | (df.dataset == "OpenStack")].reset_index(drop=True)
dA = analyse(UA, "Universe A: BGL alert_tag groups + OpenStack ERROR/WARN/CRITICAL patterns", out)
UB = df[df.kind != "pattern_c"].reset_index(drop=True)
dB = analyse(UB, "Universe B: all problem groups (BGL alert_tag + BGL untagged FATAL/ERROR/WARN/SEVERE/FAILURE patterns + OpenStack patterns)", out)
UC = df[df.kind != "pattern"].reset_index(drop=True)
dC = analyse(UC, "Universe C: as B but BGL multi-line dumps collapsed (same node + same epoch second -> first line's pattern)", out)
open("r4e_rank_output.md", "w").write("\n".join(out))
dA.to_csv("r4e_universeA_scores.csv", index=False)
print("\n".join(out))
