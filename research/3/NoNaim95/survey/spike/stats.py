"""Wilson intervals and pass@k / pass^k numbers used in findings.md."""
from math import sqrt, comb
def wilson(s, n, z=1.96):
    p = s / n; d = 1 + z*z/n; c = (p + z*z/(2*n)) / d; h = z*sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    return max(0, c-h), min(1, c+h)
for s, n in [(5,5),(4,5),(3,5),(10,10),(9,10),(27,30),(30,30),(40,45),(45,45),(200,225),(215,225)]:
    lo, hi = wilson(s, n); print(f"wilson {s}/{n}: p={s/n:.3f} 95% CI [{lo:.3f}, {hi:.3f}]")
# unbiased pass@k / pass^k estimators from n trials with c successes (Chen et al. 2021; tau-bench)
def pass_at_k(n, c, k): return 1 - comb(n-c, k)/comb(n, k)
def pass_hat_k(n, c, k): return comb(c, k)/comb(n, k)
for p in (0.95, 0.9, 0.8, 0.75):
    print(f"per-trial p={p}: pass^3={p**3:.3f} pass^5={p**5:.3f}  pass@3={1-(1-p)**3:.3f}")
print("n=5,c=4: pass@1=%.2f pass^2=%.2f pass^5=%.2f" % (pass_at_k(5,4,1), pass_hat_k(5,4,2), pass_hat_k(5,4,5)))
# minimum detectable difference (paired, rough): with 45 questions, SE of a proportion around 0.85
import math
for n in (30, 45, 60):
    se = math.sqrt(0.85*0.15/n); print(f"n={n} questions at p=0.85: SE={se:.3f}, ~95% half-width {1.96*se:.3f}")
