"""Between-round CAT primitives + the round-static scoring-order builder. Both terms of the
acquisition score and the round order: ``docs/methods/verdict-resolution.md``."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from promptpotter.domain.scoring import is_hit
from promptpotter.shared import sigmoid

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler

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
    v = var_c + se_delta_s * se_delta_s
    return sigmoid((mu_c - delta_s) / math.sqrt(1.0 + _PROBIT_SCALE * v))


def update_theta_posterior(
    mu: float, var: float, delta_s: float, se_delta_s: float, hit: bool
) -> tuple[float, float]:
    """One Newton-step Gaussian update on ``θ_c`` given ``(δ_s, se_δ_s, hit)``: a 1PL likelihood
    marginalized over δ_s via probit. As ``se_delta_s → 0`` this is the plain 1PL Laplace update."""
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
    return decision_information_gain(
        mu_c, var_c, mu_s, var_s, delta_s, se_delta_s
    ) + delta_learning_gain(mu_c, var_c, delta_s, se_delta_s)


def build_round_order(
    seed_grades: Mapping[int, float],
    ruler: DeltaRuler | None,
    sample_ids: Sequence[int],
) -> list[int]:
    # An unmeasured sample stands at the ruler's OWN centre, not at 0.0. Zero is a position on
    # this scale, not a neutral one — δ is centred wherever the bank's difficulty puts it — and
    # the miss stratum sorts ascending, so grading an unknown cell 0.0 puts it ahead of every
    # known-discriminating one and spends the round on cells no arm solves. A cold ruler is flat,
    # so everything ties at 0.0 and the order falls back to sample id.
    miss_stratum: list[int] = []
    hit_stratum: list[int] = []
    for sid in sample_ids:
        (hit_stratum if is_hit(seed_grades.get(sid)) else miss_stratum).append(sid)

    centre = ruler.mu_delta if ruler is not None else 0.0
    known = ruler.delta if ruler is not None else {}

    def _delta(sid: int) -> float:
        return known.get(sid, centre)

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
