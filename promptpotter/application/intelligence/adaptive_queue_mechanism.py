"""Between-round CAT primitives + the round-static scoring-order builder.

``pick_value = decision_information_gain + delta_learning_gain`` is the BETWEEN-round 1PL
Rasch acquisition score: the first term is MI between the next outcome and the verdict
``θ_c > θ_s`` (Garivier-Kaufmann 2016 Track-and-Stop in the means-known limit), the
second restores the parameter-information a verdict-only term omits, so under-measured
samples stay informative while resolved ones are never re-promoted. ``build_round_order``
is the WITHIN-round order and front-loads seed-MISS samples because a DISCORDANT pair
carries nearly all the information about a candidate's ability RELATIVE to the seed —
a shared hit or a shared miss moves the paired posterior barely at all; a seed-HIT
regression probe rides every 4th slot.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from promptpotter.domain.scoring import is_hit
from promptpotter.shared import sigmoid

__all__ = [
    "build_round_order",
    "decision_information_gain",
    "delta_learning_gain",
    "expected_order",
    "marginal_hit_probability",
    "pick_value",
    "update_theta_posterior",
]

# Probit scale for ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))``.
_PROBIT_SCALE = math.pi / 8.0


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
    return sigmoid((mu_c - delta_s) / math.sqrt(1.0 + _PROBIT_SCALE * v))


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
    p = sigmoid((mu - delta_s) / scale)
    score = ((1.0 if hit else 0.0) - p) / scale
    info = 1.0 / var + p * (1.0 - p) / (scale * scale)
    new_var = max(1.0 / info, 1e-6)
    new_mu = mu + new_var * score
    return new_mu, new_var


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
    p_bar = sigmoid(m / math.sqrt(1.0 + _PROBIT_SCALE * v))
    return _binary_entropy(p0) - (
        p_bar * _binary_entropy(p_hit) + (1.0 - p_bar) * _binary_entropy(p_miss)
    )


def delta_learning_gain(
    mu_c: float,
    var_c: float,
    delta_s: float,
    se_delta_s: float,
) -> float:
    """Expected entropy reduction (nats) in the sample difficulty ``δ_s`` from one measurement.

    The non-myopic value the verdict-only :func:`decision_information_gain` omits. One Bernoulli
    outcome adds Fisher info ``p(1−p)/scale²`` to the prior δ-precision ``1/se_δ²``, so the
    Gaussian entropy drop is ``½·ln(1 + se_δ²·p(1−p)/scale²)`` with
    ``scale² = 1 + π·se_δ²/8`` and ``p`` the probit-marginal hit probability. Largest for
    **under-measured** samples (large ``se_δ``) whose outcome is genuinely uncertain
    (``p≈0.5``); ``→0`` for well-measured samples (small ``se_δ``) AND for resolved
    always-hit / always-miss samples (``p(1−p)→0``) — so it explores the unknown without
    re-promoting samples the run has already pinned.
    """
    p = marginal_hit_probability(mu_c=mu_c, var_c=var_c, delta_s=delta_s, se_delta_s=se_delta_s)
    scale_sq = 1.0 + _PROBIT_SCALE * se_delta_s * se_delta_s
    se_sq = se_delta_s * se_delta_s
    return 0.5 * math.log1p(se_sq * p * (1.0 - p) / scale_sq)


def pick_value(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_s: float,
    se_delta_s: float,
) -> float:
    """Total acquisition score for one sample (nats): verdict decision-IG + δ-learning gain.

    ``decision_information_gain`` resolves ``θ_c > θ_s``; :func:`delta_learning_gain` values
    sharpening the sample's difficulty. Without the second term the seed-centred first pick
    degenerates to "prefer lowest ``se_δ``" and starves the unmeasured headroom — see the
    module docstring.
    """
    return decision_information_gain(
        mu_c, var_c, mu_s, var_s, delta_s, se_delta_s
    ) + delta_learning_gain(mu_c, var_c, delta_s, se_delta_s)


def _ruler_delta(entry: float | tuple[float, float]) -> float:
    """δ from a ruler value — a bare float is 1PL; a 2PL entry is ``(δ, a)``."""
    return float(entry[0]) if isinstance(entry, tuple) else float(entry)


def build_round_order(
    seed_grades: Mapping[int, float],
    delta_ruler: Mapping[int, float | tuple[float, float]],
    sample_ids: Sequence[int],
) -> list[int]:
    """One deterministic shared scoring order for a round — built to kill dead
    candidates in as few samples as possible.

    Partition by the seed's outcome: **MISS-stratum** (seed grade < 1.0, or not
    yet measured by the seed — an unknown is a potential win, and fronting it
    also warms the per-sample backfill earliest) vs **HIT-stratum** (seed grade
    ≥ 1.0). Every 4th position takes the next HIT-stratum sample; all other
    positions take the next MISS-stratum sample; when either stratum runs dry
    the remainder of the other follows.

    Why k=4: pure miss-first defers all regression evidence past the miss block,
    so a candidate that is winning its discordant pairs while quietly regressing on
    the seed's hits looks strong for most of the round. k=4 costs a decisive ε-stop a
    handful of extra samples and buys a regression probe inside the first
    ``elimination_n_min`` window plus steady loss accrual for regressors.

    Within the MISS-stratum: ascending δ (easiest win opportunities first — a
    live candidate proves itself immediately, and a dead one's misses on the
    easiest wins are the strongest futility evidence). Within the HIT-stratum:
    descending δ (likeliest regression points first). Cold ruler → δ = 0;
    ties → ascending sample id. Pure function of (seed grades, ruler, ids), so
    a resumed round re-derives the identical order with no recorded sidecar.
    """
    miss_stratum: list[int] = []
    hit_stratum: list[int] = []
    for sid in sample_ids:
        (hit_stratum if is_hit(seed_grades.get(sid)) else miss_stratum).append(sid)

    def _delta(sid: int) -> float:
        entry = delta_ruler.get(sid)
        return _ruler_delta(entry) if entry is not None else 0.0

    miss_stratum.sort(key=lambda sid: (_delta(sid), sid))
    hit_stratum.sort(key=lambda sid: (-_delta(sid), sid))

    order: list[int] = []
    mi, hi = 0, 0
    for pos in range(1, len(sample_ids) + 1):
        take_hit = pos % 4 == 0
        if take_hit and hi < len(hit_stratum) and mi < len(miss_stratum):
            order.append(hit_stratum[hi])
            hi += 1
        elif mi < len(miss_stratum):
            order.append(miss_stratum[mi])
            mi += 1
        else:
            order.append(hit_stratum[hi])
            hi += 1
    return order


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
