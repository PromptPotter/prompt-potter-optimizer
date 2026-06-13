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
    z = gen.standard_normal((n_samples, len(cand_ids)))
    draws = means[None, :] + ses[None, :] * z
    argmax_idx = draws.argmax(axis=1)
    counts = np.bincount(argmax_idx, minlength=len(cand_ids))
    probs = counts / float(n_samples)
    return {cid: float(probs[i]) for i, cid in enumerate(cand_ids)}


def pobb_should_stop(p_best: float, epsilon: float) -> bool:
    """Trivial threshold check — kept as a named function for call-site clarity."""
    return p_best < epsilon


# --- Paired-difference posterior — cand-vs-prior on shared sample set ---


def _paired_diff_posterior(diffs: list[float]) -> tuple[float, float]:
    """Normal posterior (mean, se) on the mean paired difference.

    SE floored at ``1/(4n)`` — same Beta-Binomial bound as ``_normal_posterior``.
    """
    return _normal_posterior(diffs)


def paired_better_probabilities(
    candidate_scores: list[float],
    paired_priors: dict[str, list[float]],
    n_samples: int = 1000,
    rng: np.random.Generator | None = None,
) -> dict[str, float]:
    """Per-prior posterior probability that ``candidate > prior`` on shared samples.

    For each prior ``p`` we compute paired differences ``d_i = c_i − p_i``
    over the shared sample set, fit a Normal posterior on the mean
    difference, and Monte-Carlo P(mean_d > 0). All priors must have the
    same length as ``candidate_scores`` — caller is responsible for the
    paired alignment (``PoBBCheck.check`` does this via
    ``priors_by_sample``).

    Returns ``{prior_id: P(cand > prior_id)}``. The caller derives
    ``p_best = min(values())`` — the most pessimistic per-prior comparison
    is an upper bound on ``P(cand is best of all)`` and the right
    elimination criterion. Empty input returns an empty dict.

    Args:
        candidate_scores: candidate's per-sample fitness on the shared set.
        paired_priors: prior_id → paired fitness list (same length as
            ``candidate_scores``).
        n_samples: Monte Carlo draws per prior; MC error ≈ ``√(p(1−p)/N)``.
        rng: optional pre-seeded numpy Generator for reproducible draws.
    """
    if not paired_priors:
        return {}
    n_cand = len(candidate_scores)
    gen = rng if rng is not None else np.random.default_rng()
    out: dict[str, float] = {}
    for prior_id, prior_scores in paired_priors.items():
        if len(prior_scores) != n_cand:
            raise ValueError(
                f"paired_better_probabilities: prior {prior_id!r} has "
                f"{len(prior_scores)} scores; candidate has {n_cand}"
            )
        diffs = [c - p for c, p in zip(candidate_scores, prior_scores, strict=True)]
        mean_d, se_d = _paired_diff_posterior(diffs)
        z = gen.standard_normal(n_samples)
        draws = mean_d + se_d * z
        out[prior_id] = float((draws > 0.0).mean())
    return out


def paired_diff_posterior(
    candidate_scores: list[float],
    prior_scores: list[float],
) -> tuple[float, float, int]:
    """Closed-form paired-difference posterior summary; for telemetry.

    Returns ``(mean_d, se_d, n_paired)``. Mirrors the math of
    ``paired_better_probabilities`` without the MC draw, so the round
    audit record can carry the per-prior diff posterior without rolling
    fresh randomness.
    """
    n = len(candidate_scores)
    if len(prior_scores) != n:
        raise ValueError(
            f"paired_diff_posterior: prior has {len(prior_scores)} scores; candidate has {n}"
        )
    diffs = [c - p for c, p in zip(candidate_scores, prior_scores, strict=True)]
    mean_d, se_d = _paired_diff_posterior(diffs)
    return (mean_d, se_d, n)


__all__ = [
    "min_detectable_effect",
    "paired_better_probabilities",
    "paired_diff_posterior",
    "pobb_should_stop",
    "posterior_best_probabilities",
    "proportion_test",
    "wilson_ci",
]
