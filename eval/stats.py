"""Statistics for the eval report (T17): Wilson intervals, pass@k and pass^k.

Reuses research/3-NoNaim95 `survey/spike/stats.py`. The estimators are the unbiased ones from n trials
with c passes: pass@k (Chen et al. 2021, at least one of k trials passes) and pass^k (tau-bench, all k
trials pass). Confidence intervals are computed per question, never by treating every trial as
independent, which gives a falsely narrow interval (Miller 2024, "Adding error bars to evals").
"""

from __future__ import annotations

import math

Z95 = 1.959963984540054
"""The two-sided 95% normal quantile."""


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """The Wilson score interval for `successes` out of `n`; (0.0, 1.0) when n is 0."""
    if n < 0 or not 0 <= successes <= n:
        raise ValueError(f"need 0 <= successes <= n, got successes={successes}, n={n}")
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    low = 0.0 if successes == 0 else max(0.0, centre - half)  # exact at the ends, no rounding residue
    high = 1.0 if successes == n else min(1.0, centre + half)
    return low, high


def pass_at_k(n: int, c: int, k: int) -> float | None:
    """The chance that at least one of k trials drawn from n (c passing) passes; None when k > n."""
    _check(n, c, k)
    if k > n:
        return None
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float | None:
    """pass^k: the chance that all k trials drawn from n (c passing) pass; None when k > n."""
    _check(n, c, k)
    if k > n:
        return None
    return math.comb(c, k) / math.comb(n, k)


def _check(n: int, c: int, k: int) -> None:
    if k < 1 or n < 0 or not 0 <= c <= n:
        raise ValueError(f"need k >= 1 and 0 <= c <= n, got n={n}, c={c}, k={k}")
