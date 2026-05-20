"""Online adaptive sample picker — 1PL Rasch CAT with an information-gain objective.

After each observation the picker re-estimates the candidate's latent-ability
posterior ``θ_c ~ N(μ_c, var_c)`` and re-picks the next sample. Two objectives
share the module, selected by ``OptimizationConfig.picker_objective``:

* **model** (default) — Expected Information Gain: the entropy reduction of the
  hierarchical IRT posterior from one Bernoulli measurement. It reads the
  candidate-ability variance *and* the sample-difficulty SE that ``fit_rasch``
  already returns, so a barely-measured sample (large ``se_δ``) ranks high —
  measuring it sharpens the model. As ``se_δ → 0`` the objective collapses to
  the textbook Maximum-Fisher-Information ranking (Lord 1980 CAT).

* **decision** — the mutual information between the next outcome and the
  keep/abort verdict ``θ_c > θ_s`` against the seed. Decision-aligned; the
  means-known limit recovers Bernoulli Chernoff information
  (Garivier-Kaufmann 2016 Track-and-Stop).

Both objectives are knob-free and computed over the posterior. Pure module:
no I/O, no mutation of inputs.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

__all__ = [
    "decision_information_gain",
    "expected_information_gain",
    "expected_order_decision",
    "expected_order_eig",
    "next_sample_decision",
    "next_sample_eig",
    "posterior_from_outcomes",
    "predictive_hit_prob",
    "update_theta_posterior",
]

# Probit scale for the logistic-Gaussian integral
# ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))``.
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


def update_theta_posterior(mu: float, var: float, delta_s: float, hit: bool) -> tuple[float, float]:
    """One Newton-step Gaussian update on ``θ_c`` given (δ_s, hit).

    Posterior log-density under a Gaussian prior ``θ ~ N(μ, σ²)`` and
    Bernoulli 1PL likelihood:

        log p(θ | data) = -(θ - μ)²/(2σ²) + y·(θ - δ_s) - log(1 + e^(θ - δ_s))

    where ``y = 1`` if hit else ``0``. One Newton step anchored at the
    prior mean ``θ = μ`` gives the Laplace-approximation posterior:

        score   = y - σ(μ - δ_s)                      (first deriv)
        info    = 1/σ² + σ(μ - δ_s)·(1 - σ(μ - δ_s))  (observed Fisher)
        σ'²     = 1 / info
        μ'      = μ + σ'² · score

    Closed form, no iteration. Variance is floored at ``1e-6`` so
    repeated updates on a confidently-pinned θ stay numerically stable.
    """
    p = _sigmoid(mu - delta_s)
    score = (1.0 if hit else 0.0) - p
    info = 1.0 / var + p * (1.0 - p)
    new_var = max(1.0 / info, 1e-6)
    new_mu = mu + new_var * score
    return new_mu, new_var


def posterior_from_outcomes(
    prior_mu: float,
    prior_var: float,
    outcomes: Iterable[tuple[float, bool]],
) -> tuple[float, float]:
    """Fold ``update_theta_posterior`` across a sequence of (δ_s, hit) pairs.

    Stateless recomputation — callers don't persist the running posterior;
    they fold over the candidate's measured outcomes from scratch each
    time the picker fires. With ≤ ``len(dataset)`` updates per candidate
    and each update O(1), this is microseconds per call.
    """
    mu, var = prior_mu, prior_var
    for delta_s, hit in outcomes:
        mu, var = update_theta_posterior(mu, var, delta_s, hit)
    return mu, var


# ---------------------------------------------------------------------------
# Model objective — Expected Information Gain
# ---------------------------------------------------------------------------


def predictive_hit_prob(mu_c: float, var_c: float, delta_s: float, se_delta_s: float) -> float:
    """Posterior-predictive hit probability of candidate ``c`` on sample ``s``.

    Marginalizes the 1PL likelihood ``σ(θ_c − δ_s)`` over the Gaussian
    posteriors ``θ_c ~ N(μ_c, var_c)`` and ``δ_s ~ N(δ̂_s, se_δ_s²)`` with the
    probit approximation ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))``.
    """
    m = mu_c - delta_s
    v = var_c + se_delta_s * se_delta_s
    return _sigmoid(m / math.sqrt(1.0 + _PROBIT_SCALE * v))


def expected_information_gain(
    mu_c: float, var_c: float, delta_s: float, se_delta_s: float
) -> float:
    """Expected information gain of one measurement of candidate ``c`` on sample ``s``.

    The entropy reduction of the joint ``(θ_c, δ_s)`` Gaussian posterior from a
    single Bernoulli observation. A measurement informs only the difference
    ``θ_c − δ_s`` (observed-info contribution ``w̄·[[1,−1],[−1,1]]``); the
    priors break the rank-1 degeneracy and the joint-entropy reduction is
    closed form::

        EIG = ½ · log(1 + w̄ · (var_c + se_δ_s²))

    with ``w̄ = p̄(1−p̄)`` the predictive Bernoulli variance. Large ``se_δ_s``
    (a barely-measured sample) ⇒ large EIG; as ``se_δ_s → 0`` the objective is
    monotone in ``w̄`` — the Maximum-Fisher-Information ranking, recovered
    exactly as the δ-known limit.
    """
    v = var_c + se_delta_s * se_delta_s
    p_bar = predictive_hit_prob(mu_c, var_c, delta_s, se_delta_s)
    w_bar = p_bar * (1.0 - p_bar)
    return 0.5 * math.log1p(w_bar * v)


def next_sample_eig(
    mu_c: float,
    var_c: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    remaining: set[int],
) -> int | None:
    """Pick the remaining sample with the highest expected information gain.

    Returns ``None`` when ``remaining`` is empty (loop-exit signal). Ties break
    by ascending sample id (deterministic). A sample absent from the maps falls
    back to a centred, fully-uncertain prior — it sorts high, gets measured,
    and enters the next fit.
    """
    if not remaining:
        return None
    return max(
        remaining,
        key=lambda sid: (
            expected_information_gain(
                mu_c, var_c, delta_map.get(sid, 0.0), delta_se_map.get(sid, 1.0)
            ),
            -sid,
        ),
    )


def expected_order_eig(
    mu_c: float,
    var_c: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    sample_ids: Iterable[int],
) -> list[int]:
    """Full ranking of ``sample_ids`` by descending expected information gain.

    Telemetry-only snapshot consumed by ``dashboard.json::hard_sample_order``
    so the webapp's hard-samples table mirrors the picker's expected sequence
    at the candidate's prior ability. Live execution re-picks per step via
    :func:`next_sample_eig` once outcomes start arriving.
    """
    return sorted(
        sample_ids,
        key=lambda sid: (
            -expected_information_gain(
                mu_c, var_c, delta_map.get(sid, 0.0), delta_se_map.get(sid, 1.0)
            ),
            sid,
        ),
    )


# ---------------------------------------------------------------------------
# Decision objective — mutual information with the keep/abort verdict
# ---------------------------------------------------------------------------


def decision_information_gain(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_s: float,
    se_delta_s: float,
) -> float:
    """Mutual information between one measurement of ``c`` on ``s`` and the verdict.

    The verdict is the keep/abort decision ``θ_c > θ_s`` against the seed.
    ``I(Y_s ; verdict) = H_b(P₀) − E_Y[H_b(P | Y)]`` where
    ``P = Φ((μ_c − μ_s) / √(var_c + var_s))`` is the probability the candidate
    beats the seed, the one-step Newton update folds a hit / miss into ``θ_c``,
    and the outcome ``Y`` is weighted by the predictive hit probability.
    Knob-free; the means-known limit recovers Bernoulli Chernoff information.
    """
    p0 = _normal_cdf((mu_c - mu_s) / math.sqrt(var_c + var_s))
    mu_hit, var_hit = update_theta_posterior(mu_c, var_c, delta_s, True)
    mu_miss, var_miss = update_theta_posterior(mu_c, var_c, delta_s, False)
    p_hit = _normal_cdf((mu_hit - mu_s) / math.sqrt(var_hit + var_s))
    p_miss = _normal_cdf((mu_miss - mu_s) / math.sqrt(var_miss + var_s))
    p_bar = predictive_hit_prob(mu_c, var_c, delta_s, se_delta_s)
    return _binary_entropy(p0) - (
        p_bar * _binary_entropy(p_hit) + (1.0 - p_bar) * _binary_entropy(p_miss)
    )


def next_sample_decision(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    remaining: set[int],
) -> int | None:
    """Pick the remaining sample maximizing decision information gain.

    Returns ``None`` when ``remaining`` is empty. Ties break by ascending
    sample id. Missing samples fall back to a centred, fully-uncertain prior.
    """
    if not remaining:
        return None
    return max(
        remaining,
        key=lambda sid: (
            decision_information_gain(
                mu_c, var_c, mu_s, var_s, delta_map.get(sid, 0.0), delta_se_map.get(sid, 1.0)
            ),
            -sid,
        ),
    )


def expected_order_decision(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    sample_ids: Iterable[int],
) -> list[int]:
    """Full ranking by descending decision information gain.

    Telemetry snapshot peer of :func:`expected_order_eig` — surfaced on
    ``dashboard.json::hard_sample_order`` when the picker objective is
    ``decision``.
    """
    return sorted(
        sample_ids,
        key=lambda sid: (
            -decision_information_gain(
                mu_c, var_c, mu_s, var_s, delta_map.get(sid, 0.0), delta_se_map.get(sid, 1.0)
            ),
            sid,
        ),
    )
