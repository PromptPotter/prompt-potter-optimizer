"""Online adaptive sample picker — 1PL Rasch CAT with one knob-free objective.

After each observation the picker re-estimates the candidate's latent-ability
posterior ``θ_c ~ N(μ_c, var_c)`` and re-picks the next sample. It is one-step
greedy and has **one** objective:

    score(s) = decision_information_gain(s)

— the mutual information between the next outcome and the keep/abort verdict
``θ_c > θ_s`` against the seed. Peaks where an outcome would move the stop
decision — the difficulty band from the candidate's ability up to the seed's —
and falls to zero on trivially-easy (hit predictable) and super-hard (miss
predictable) samples, the wasted measurements. The means-known limit recovers
Bernoulli Chernoff information (Garivier-Kaufmann 2016 Track-and-Stop).

The score reads the ``se_δ``-aware ability update: an outcome on a
poorly-characterized sample (large ``se_δ``) updates ``θ_c`` less, because it
cannot be told apart from a hard-sample miss. Knob-free; pure module — no I/O,
no mutation of inputs. The same scoring path is called by the live picker
(``loop.py::_next_sample``) and the persisted ranking writer
(``hard_sample_sorter::_pick_score_under_prior``); both serialize the same
statistical model at different conditioning points.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

__all__ = [
    "decision_information_gain",
    "expected_order",
    "next_sample",
    "pick_value",
    "posterior_from_outcomes",
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


def update_theta_posterior(
    mu: float, var: float, delta_s: float, se_delta_s: float, hit: bool
) -> tuple[float, float]:
    """One Newton-step Gaussian update on ``θ_c`` given (δ_s, se_δ_s, hit).

    The 1PL likelihood is ``σ(θ_c − δ_s)``, but ``δ_s`` is itself only known up
    to ``δ_s ~ N(δ̂_s, se_δ_s²)``. Marginalizing the likelihood over that
    difficulty posterior with the probit approximation flattens the logistic by
    ``s = √(1 + π·se_δ_s²/8)``::

        p     = σ((μ − δ̂_s) / s)
        score = (y − p) / s                     (first deriv of marginal log-lik)
        info  = 1/σ² + p·(1 − p) / s²           (observed Fisher + prior precision)
        σ'²   = 1 / info
        μ'    = μ + σ'² · score

    where ``y = 1`` if hit else ``0``. A poorly-characterized sample (large
    ``se_delta_s`` ⇒ large ``s``) damps both the score and the information, so
    its outcome moves ``θ_c`` less — you cannot tell a candidate-miss from a
    hard-sample miss. As ``se_delta_s → 0`` (``s → 1``) this is the plain 1PL
    Laplace update. Closed form, no iteration; variance floored at ``1e-6``.
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
    """Fold ``update_theta_posterior`` across a sequence of (δ_s, se_δ_s, hit) triples.

    Stateless recomputation — callers don't persist the running posterior;
    they fold over the candidate's measured outcomes from scratch each time the
    picker fires. With ≤ ``len(dataset)`` updates per candidate and each update
    O(1), this is microseconds per call.
    """
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
    """Mutual information between one measurement of ``c`` on ``s`` and the verdict.

    The verdict is the keep/abort decision ``θ_c > θ_s`` against the seed.
    ``I(Y_s ; verdict) = H_b(P₀) − E_Y[H_b(P | Y)]`` where
    ``P = Φ((μ_c − μ_s) / √(var_c + var_s))`` is the probability the candidate
    beats the seed, the one-step Newton update folds a hit / miss into ``θ_c``,
    and the outcome ``Y`` is weighted by the marginal hit probability under the
    candidate's posterior. Knob-free; the means-known limit recovers Bernoulli
    Chernoff information.
    """
    p0 = _normal_cdf((mu_c - mu_s) / math.sqrt(var_c + var_s))
    mu_hit, var_hit = update_theta_posterior(mu_c, var_c, delta_s, se_delta_s, True)
    mu_miss, var_miss = update_theta_posterior(mu_c, var_c, delta_s, se_delta_s, False)
    p_hit = _normal_cdf((mu_hit - mu_s) / math.sqrt(var_hit + var_s))
    p_miss = _normal_cdf((mu_miss - mu_s) / math.sqrt(var_miss + var_s))
    # Marginal hit probability for outcome weighting: probit approximation
    # ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))`` over the joint candidate /
    # sample uncertainty.
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
    """The picker's single objective: mutual information with the keep/abort verdict.

    Thin alias over :func:`decision_information_gain` kept as the public name
    for picker call sites so the call shape (``pick_value(...)``) reads as
    "the picker's score for this sample" without leaking the math into the
    caller. In nats.
    """
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
    """Pick the remaining sample with the highest pick-value.

    Returns ``None`` when ``remaining`` is empty (loop-exit signal). Ties break
    by ascending sample id (deterministic, matching :func:`expected_order`). A
    sample absent from the maps falls back to a centred, fully-uncertain
    prior — it sorts high, gets measured, and enters the next fit.
    """
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
    """Full ranking of ``sample_ids`` by descending pick-value.

    Serialized into ``dashboard.json::hard_sample_order`` so the webapp's
    hard-samples table mirrors the picker's ranking at the ``(μ_c, var_c)``
    the caller supplies — the candidate's live posterior during a round, its
    seed-centred prior for the round-boundary ranking. The live pick comes
    from :func:`next_sample` on the same posterior. Ties break by ascending
    sample id — the same head as :func:`next_sample`.
    """
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
