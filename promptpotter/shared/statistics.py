"""Pure statistics utilities — paired-difference posterior, t-critical, MDE, Bayesian PoBB.

Leaf-level: depends only on stdlib + numpy + scipy. No domain or service imports.
Requires ``scipy`` (``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import math

import numpy as np


def t_critical(df: int, alpha: float = 0.05) -> float:
    """Two-sided Student-t critical value ``t_{df, 1-alpha/2}`` for a mean whose SE was
    estimated from the same few observations — the normal quantile understates the interval
    at the panel sizes the paired verdicts run on (≈7 cells → 2.45 vs 1.96)."""
    if df < 1:
        raise ValueError(f"t_critical: df must be >= 1, got {df}")

    from scipy.stats import t

    return float(t.ppf(1 - alpha / 2, df))


def min_detectable_effect(se: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest effect detectable at *alpha* / *power*, given the estimator's OWN standard error.

    Takes the SE rather than a sample count, because the caller always has it. The old form took
    ``n`` and assumed worst-case BINOMIAL variance — ``(z_a + z_b)·sqrt(0.25/n)`` — which is the
    right formula for a proportion and the wrong one for every caller this has: an L4 outer round
    pairs graded composites, and the pairing is precisely what shrinks the variance below the
    binomial worst case. Measured on the 6-cell panel it reported 0.57 where the paired MDE was
    0.15, a 3.8x overstatement of how much headroom the panel still needed — while the correct
    ``se`` sat three lines above the call site, already computed."""
    if se <= 0.0:
        return 0.0

    from scipy.stats import norm

    return float((norm.ppf(1 - alpha / 2) + norm.ppf(power)) * se)


# --- PoBB: Posterior-of-Being-Best (Russo 2016 / Top-Two Thompson family) ---


def _normal_posterior(scores: list[float]) -> tuple[float, float]:
    """Normal posterior (mean, se) on the population mean of *scores*.

    SE clipped to ``1/(4n)`` — Beta-Binomial worst-case SE on a [0,1]
    bounded score. Protects against the small-n binary regime (e.g. 4/4
    hits has empirical ``var=0`` and would collapse the posterior to a
    point mass, prematurely stopping exploration).
    """
    n = len(scores)
    if n == 0:
        return (0.0, 1.0)
    arr = np.asarray(scores, dtype=np.float64)
    mean = float(arr.mean())
    if n == 1:
        return (mean, 0.5)
    variance = float(arr.var(ddof=1))
    se = math.sqrt(variance / n)
    se_floor = 1.0 / (4.0 * n)
    return (mean, max(se, se_floor))


# --- Paired-difference posterior — cand-vs-prior on shared sample set ---


def paired_diff_posterior(
    candidate_scores: list[float],
    prior_scores: list[float],
) -> tuple[float, float, int]:
    """Closed-form paired-difference posterior summary; for telemetry.

    Returns ``(mean_d, se_d, n_paired)`` — the per-prior diff posterior the round
    audit record (and the ``l1_score`` p_value diagnostic) carries, computed on
    the candidate-vs-prior paired fitness differences over a shared sample set.
    """
    n = len(candidate_scores)
    if len(prior_scores) != n:
        raise ValueError(
            f"paired_diff_posterior: prior has {len(prior_scores)} scores; candidate has {n}"
        )
    diffs = [c - p for c, p in zip(candidate_scores, prior_scores, strict=True)]
    mean_d, se_d = _normal_posterior(diffs)
    return (mean_d, se_d, n)


def mean_ci(values: list[float], alpha: float = 0.05) -> tuple[float, float, float]:
    """Normal-CLT confidence interval on the mean of *values* — the same posterior
    ``_normal_posterior`` gives PoBB and ``paired_diff_posterior``, expressed as
    ``(mean, ci_lo, ci_hi)`` on the values' own [0,1] scale (composite fitness), not a
    difference. ``n=0`` is degenerate: ``(0.0, 0.0, 0.0)``.
    """
    if not values:
        return (0.0, 0.0, 0.0)

    from scipy.stats import norm

    mean, se = _normal_posterior(values)
    z = norm.ppf(1 - alpha / 2)
    return (mean, mean - z * se, mean + z * se)


__all__ = [
    "mean_ci",
    "min_detectable_effect",
    "paired_diff_posterior",
    "t_critical",
]
