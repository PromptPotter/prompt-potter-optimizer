"""Pure statistics utilities — Wilson CI, two-proportion z-test, MDE, sequential elimination.

Leaf-level: depends only on stdlib + scipy. No domain or service imports.
Requires ``scipy`` (``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import math
from typing import Any


def wilson_ci(hits: int, total: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score confidence interval for a binomial proportion.

    Returns (lower, upper) as fractions in [0, 1].
    """
    if total == 0:
        return (0.0, 0.0)

    from scipy.stats import norm  # type: ignore[import-untyped]

    z = norm.ppf(1 - alpha / 2)
    p_hat = hits / total
    denom = 1 + z**2 / total
    center = (p_hat + z**2 / (2 * total)) / denom
    margin = z * math.sqrt(p_hat * (1 - p_hat) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def proportion_test(
    hits_a: int,
    total_a: int,
    hits_b: int,
    total_b: int,
) -> float:
    """Two-proportion z-test p-value (two-sided).

    Returns p-value, or 1.0 when the test is degenerate.
    """
    if total_a == 0 or total_b == 0:
        return 1.0

    from scipy.stats import norm  # type: ignore[import-untyped]

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

    from scipy.stats import norm  # type: ignore[import-untyped]

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    mde = (z_alpha + z_beta) * math.sqrt(0.25 / n)
    return min(mde, 1.0)


def min_sample_size(target_mde: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Minimum sample size to detect a given effect size.

    Algebraic inverse of ``min_detectable_effect()``:
    ``n = ceil(((z_alpha + z_beta) / mde)^2 * 0.25)``.

    Uses worst-case variance (p=0.5).
    """
    if target_mde <= 0:
        return 1

    from scipy.stats import norm  # type: ignore[import-untyped]

    z_alpha = norm.ppf(1 - alpha / 2)
    z_beta = norm.ppf(power)
    n = math.ceil(((z_alpha + z_beta) / target_mde) ** 2 * 0.25)
    return max(n, 1)


# ---------------------------------------------------------------------------
# Sequential candidate elimination — paired Welch's t-test + Holm-Bonferroni
# ---------------------------------------------------------------------------


def _paired_ttest_pvalue(current: list[float], prior: list[float]) -> float:
    """One-sided paired t-test p-value (H_a: prior is better than current).

    Uses ``scipy.stats.ttest_rel`` on the shared prefix, then converts
    the two-sided p-value to one-sided.  Returns 1.0 when variance of
    differences is zero (identical score vectors).
    """
    from scipy.stats import ttest_rel  # type: ignore[import-untyped]

    n = min(len(current), len(prior))
    if n < 2:
        return 1.0

    stat, p_two = ttest_rel(prior[:n], current[:n])

    if math.isnan(p_two):
        return 1.0

    if stat > 0:
        return p_two / 2
    return 1 - p_two / 2


def _holm_bonferroni(p_values: list[float], alpha: float) -> list[bool]:
    """Holm-Bonferroni step-down correction.

    Returns a list of booleans (same order as *p_values*) indicating
    which null hypotheses are rejected at family-wise *alpha*.
    """
    m = len(p_values)
    if m == 0:
        return []

    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    rejected = [False] * m

    for rank, (orig_idx, p) in enumerate(indexed):
        threshold = alpha / (m - rank)
        if p < threshold:
            rejected[orig_idx] = True
        else:
            break

    return rejected


def should_stop_early(
    current_scores: list[float],
    prior_populations: list[list[float]],
    alpha: float = 0.05,
) -> tuple[bool, dict[str, Any]]:
    """Decide whether to eliminate the current candidate.

    Runs a one-sided paired t-test against each prior population on the
    shared query prefix, then applies Holm-Bonferroni correction.

    Returns:
        ``(stop, context)`` where *context* carries p-values and the
        index of the prior that triggered rejection (if any).
    """
    if not prior_populations:
        return False, {}

    p_values = [_paired_ttest_pvalue(current_scores, prior) for prior in prior_populations]
    rejections = _holm_bonferroni(p_values, alpha)

    ctx: dict[str, Any] = {"p_values": p_values, "rejections": rejections}

    if any(rejections):
        trigger_idx = rejections.index(True)
        ctx["triggered_by_prior"] = trigger_idx
        ctx["triggered_p"] = p_values[trigger_idx]
        return True, ctx

    return False, ctx
