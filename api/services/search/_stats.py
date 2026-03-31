"""Statistical utilities for sensitivity scan and search analysis.

Core functions (wilson_ci, proportion_test, min_detectable_effect) used by
scan advisor, early pruning, and sample sizing.  Display-only helpers
(fmt_ci, fmt_pvalue) remain in ``notebooks/campaign_lib/stats.py``.

Requires ``scipy`` (optional dependency: ``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import math


def wilson_ci(hits: int, total: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Returns (lower, upper) as fractions in [0, 1].
    """
    if total == 0:
        return (0.0, 0.0)

    from scipy.stats import norm

    z = norm.ppf(1 - alpha / 2)
    p_hat = hits / total
    denom = 1 + z**2 / total
    center = (p_hat + z**2 / (2 * total)) / denom
    margin = (
        z * math.sqrt(p_hat * (1 - p_hat) / total + z**2 / (4 * total**2)) / denom
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


def proportion_test(
    hits_a: int, total_a: int, hits_b: int, total_b: int,
) -> float:
    """Two-proportion z-test p-value (two-sided).

    Returns p-value, or 1.0 when the test is degenerate.
    """
    if total_a == 0 or total_b == 0:
        return 1.0

    from scipy.stats import norm

    p_pool = (hits_a + hits_b) / (total_a + total_b)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / total_a + 1 / total_b))
    if se < 1e-12:
        return 1.0

    z = (hits_a / total_a - hits_b / total_b) / se
    return float(2 * norm.sf(abs(z)))


def min_detectable_effect(n: int, alpha: float = 0.05, power: float = 0.8) -> float:
    """Minimum detectable effect size for a given sample size.

    Uses worst-case variance (p=0.5). Returns MDE as a fraction.
    """
    if n <= 0:
        return 1.0

    from scipy.stats import norm

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    mde = (z_alpha + z_beta) * math.sqrt(0.25 / n)
    return min(mde, 1.0)
