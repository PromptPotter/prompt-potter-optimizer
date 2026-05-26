"""Adaptive queue mechanism — 1PL Rasch CAT.

``score(s) = decision_information_gain(s)`` = MI between next outcome and verdict ``θ_c > θ_s``;
means-known limit recovers Bernoulli Chernoff information (Garivier-Kaufmann 2016 Track-and-Stop).
"""

from __future__ import annotations

import math
from collections.abc import Iterable

__all__ = [
    "decision_information_gain",
    "expected_order",
    "marginal_hit_probability",
    "next_sample",
    "pick_value",
    "posterior_from_outcomes",
    "update_theta_posterior",
]

# Probit scale for ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))``.
_PROBIT_SCALE = math.pi / 8.0


def _sigmoid(x: float) -> float:
    """Numerically-stable sigmoid (no SciPy dependency)."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


def _normal_cdf(x: float) -> float:
    """Standard-normal CDF Φ(x) via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _binary_entropy(p: float) -> float:
    """Binary (Shannon) entropy in nats; 0 at ``p ∈ {0, 1}``."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -p * math.log(p) - (1.0 - p) * math.log(1.0 - p)


def marginal_hit_probability(
    *,
    mu_c: float,
    var_c: float,
    delta_s: float,
    se_delta_s: float,
) -> float:
    """Probit-marginalized hit probability ``E[σ(θ_c − δ_s)] ≈ σ((μ_c − δ_s) / √(1 + π·(var_c + se_δ_s²)/8))``."""
    v = var_c + se_delta_s * se_delta_s
    return _sigmoid((mu_c - delta_s) / math.sqrt(1.0 + _PROBIT_SCALE * v))


def update_theta_posterior(
    mu: float, var: float, delta_s: float, se_delta_s: float, hit: bool
) -> tuple[float, float]:
    """One Newton-step Gaussian update on ``θ_c`` given (δ_s, se_δ_s, hit).

    1PL likelihood ``σ(θ_c − δ_s)`` marginalized over ``δ_s ~ N(δ̂_s, se_δ_s²)``
    via probit, flattening by ``s = √(1 + π·se_δ_s²/8)``::

        p     = σ((μ − δ̂_s) / s)
        score = (y − p) / s                     (first deriv of marginal log-lik)
        info  = 1/σ² + p·(1 − p) / s²           (observed Fisher + prior precision)
        σ'²   = 1 / info ;  μ' = μ + σ'² · score

    ``y = 1`` if hit. Large ``se_delta_s`` damps both score and info. As
    ``se_delta_s → 0`` this is the plain 1PL Laplace update. Variance floored at ``1e-6``.
    """
    scale = math.sqrt(1.0 + _PROBIT_SCALE * se_delta_s * se_delta_s)
    p = _sigmoid((mu - delta_s) / scale)
    score = ((1.0 if hit else 0.0) - p) / scale
    info = 1.0 / var + p * (1.0 - p) / (scale * scale)
    new_var = max(1.0 / info, 1e-6)
    new_mu = mu + new_var * score
    return new_mu, new_var


def posterior_from_outcomes(
    prior_mu: float,
    prior_var: float,
    outcomes: Iterable[tuple[float, float, bool]],
) -> tuple[float, float]:
    """Fold ``update_theta_posterior`` across ``(δ_s, se_δ_s, hit)`` triples. Stateless."""
    mu, var = prior_mu, prior_var
    for delta_s, se_delta_s, hit in outcomes:
        mu, var = update_theta_posterior(mu, var, delta_s, se_delta_s, hit)
    return mu, var


def decision_information_gain(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_s: float,
    se_delta_s: float,
) -> float:
    """Mutual information between one measurement of ``c`` on ``s`` and the verdict ``θ_c > θ_s``.

    ``I(Y_s ; verdict) = H_b(P₀) − E_Y[H_b(P | Y)]``;
    ``P = Φ((μ_c − μ_s) / √(var_c + var_s))``. Outcome ``Y`` weighted by the
    candidate's marginal hit probability.
    """
    p0 = _normal_cdf((mu_c - mu_s) / math.sqrt(var_c + var_s))
    mu_hit, var_hit = update_theta_posterior(mu_c, var_c, delta_s, se_delta_s, True)
    mu_miss, var_miss = update_theta_posterior(mu_c, var_c, delta_s, se_delta_s, False)
    p_hit = _normal_cdf((mu_hit - mu_s) / math.sqrt(var_hit + var_s))
    p_miss = _normal_cdf((mu_miss - mu_s) / math.sqrt(var_miss + var_s))
    # Probit ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))`` over joint (candidate, sample) variance.
    m = mu_c - delta_s
    v = var_c + se_delta_s * se_delta_s
    p_bar = _sigmoid(m / math.sqrt(1.0 + _PROBIT_SCALE * v))
    return _binary_entropy(p0) - (
        p_bar * _binary_entropy(p_hit) + (1.0 - p_bar) * _binary_entropy(p_miss)
    )


def pick_value(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_s: float,
    se_delta_s: float,
) -> float:
    """Queue-mechanism score for one sample (nats) — alias over :func:`decision_information_gain`."""
    return decision_information_gain(mu_c, var_c, mu_s, var_s, delta_s, se_delta_s)


def next_sample(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    remaining: set[int],
) -> int | None:
    """Pick the remaining sample with the highest pick-value; ``None`` ⇒ loop exit. Ties → asc sid."""
    if not remaining:
        return None
    return max(
        remaining,
        key=lambda sid: (
            pick_value(
                mu_c,
                var_c,
                mu_s,
                var_s,
                delta_map.get(sid, 0.0),
                delta_se_map.get(sid, 1.0),
            ),
            -sid,
        ),
    )


def expected_order(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    sample_ids: Iterable[int],
) -> list[int]:
    """Full ranking of ``sample_ids`` by descending pick-value; ties → asc sid."""
    return sorted(
        sample_ids,
        key=lambda sid: (
            -pick_value(
                mu_c,
                var_c,
                mu_s,
                var_s,
                delta_map.get(sid, 0.0),
                delta_se_map.get(sid, 1.0),
            ),
            sid,
        ),
    )
