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

    from scipy.stats import norm

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
    return float(min(mde, 1.0))


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


def posterior_best_from_normals(
    normals: dict[str, tuple[float, float]],
    n_samples: int = 1000,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """P(each candidate is best) given a Normal posterior ``(mean, se)`` per candidate.

    The MC argmax engine: ``n_samples`` joint draws from the independent
    per-candidate Normals, counting the argmax fraction. The estimand is the
    caller's — ``elevate_to_decisive`` feeds it the fixed-ruler θ posteriors
    (``(theta, theta_se)`` per arm); only the posterior summary is needed.
    ``n_samples`` default 1000 → MC error √(p(1-p)/N) ≈ 0.7% at p=0.05. Empty
    input → empty dict; probabilities sum to 1.0 ± MC error.
    """
    if not normals:
        return {}
    cand_ids = list(normals)
    means = np.fromiter((normals[c][0] for c in cand_ids), dtype=np.float64, count=len(cand_ids))
    ses = np.fromiter((normals[c][1] for c in cand_ids), dtype=np.float64, count=len(cand_ids))

    gen = rng if rng is not None else np.random.default_rng()
    z = gen.standard_normal((n_samples, len(cand_ids)))
    draws = means[None, :] + ses[None, :] * z
    counts = np.bincount(draws.argmax(axis=1), minlength=len(cand_ids))
    probs = counts / float(n_samples)
    return {cid: float(probs[i]) for i, cid in enumerate(cand_ids)}


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


__all__ = [
    "min_detectable_effect",
    "paired_diff_posterior",
    "posterior_best_from_normals",
    "proportion_test",
    "wilson_ci",
]
