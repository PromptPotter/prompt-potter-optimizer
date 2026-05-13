"""Pure statistics utilities — Wilson CI, two-proportion z-test, MDE, Bayesian PoBB.

Leaf-level: depends only on stdlib + numpy + scipy. No domain or service imports.
Requires ``scipy`` (``pip install -e ".[stats]"``).
"""

from __future__ import annotations

import math

import numpy as np


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


# ---------------------------------------------------------------------------
# Bayesian best-arm identification — Posterior-of-Being-Best (PoBB)
# ---------------------------------------------------------------------------
#
# One sentence: stop a candidate when its posterior probability of being the
# round's best drops below ε. Joint Normal-CLT posterior over per-candidate
# mean accuracy; argmax-over-population computed by Monte Carlo.
#
# References: Russo (2016) "Simple Bayesian Algorithms for Best Arm Identification";
# OCBA (Chen 2000); Top-Two Thompson Sampling family.
# ---------------------------------------------------------------------------


def _normal_posterior(scores: list[float]) -> tuple[float, float]:
    """Normal posterior (mean, se) on the population mean of *scores*.

    SE clipped to ``1/(4n)`` (Beta-Binomial worst-case for bounded [0,1]
    scores) so we don't over-confidently stop on small-sample binary regimes.
    """
    n = len(scores)
    if n == 0:
        return (0.0, 1.0)
    arr = np.asarray(scores, dtype=np.float64)
    mean = float(arr.mean())
    if n == 1:
        # No within-sample variance estimate; use Beta-Binomial worst-case.
        return (mean, 0.5)
    variance = float(arr.var(ddof=1))
    se = math.sqrt(variance / n)
    se_floor = 1.0 / (4.0 * n)
    return (mean, max(se, se_floor))


def posterior_best_probabilities(
    score_histories: dict[str, list[float]],
    n_samples: int = 1000,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """Posterior probability that each candidate has the round's highest mean accuracy.

    For each candidate we maintain a Normal posterior on its mean accuracy
    (CLT on observed per-sample scores). We sample ``n_samples`` joint draws
    from the independent per-candidate Normals and count, for each candidate,
    the fraction of draws in which it is argmax over the population.

    Args:
        score_histories: candidate_id → observed score list (one entry per
            measured sample).
        n_samples: Monte Carlo joint-draw count. Default 1000; MC error on
            P(best) is √(p(1-p)/N) ≈ 0.7% at p=0.05.
        rng: optional pre-seeded numpy Generator for reproducible draws.

    Returns:
        candidate_id → P(c is best). Probabilities sum to 1.0 ± MC error.
        Empty input returns an empty dict.
    """
    if not score_histories:
        return {}

    cand_ids = list(score_histories.keys())
    means = np.empty(len(cand_ids), dtype=np.float64)
    ses = np.empty(len(cand_ids), dtype=np.float64)
    for i, cid in enumerate(cand_ids):
        m, s = _normal_posterior(score_histories[cid])
        means[i] = m
        ses[i] = s

    gen = rng if rng is not None else np.random.default_rng()
    # Joint draws: shape (n_samples, n_cands). Per-candidate Normal is
    # mean[i] + se[i] * standard_normal.
    z = gen.standard_normal((n_samples, len(cand_ids)))
    draws = means[None, :] + ses[None, :] * z
    argmax_idx = draws.argmax(axis=1)
    counts = np.bincount(argmax_idx, minlength=len(cand_ids))
    probs = counts / float(n_samples)
    return {cid: float(probs[i]) for i, cid in enumerate(cand_ids)}


def pobb_should_stop(p_best: float, epsilon: float) -> bool:
    """Trivial threshold check — kept as a named function for call-site clarity."""
    return p_best < epsilon
