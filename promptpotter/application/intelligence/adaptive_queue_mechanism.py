"""Both terms of the acquisition score: ``docs/methods/verdict-resolution.md``."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from promptpotter.domain.scoring import ROW_GRADES
from promptpotter.shared.statistics import logistic

if TYPE_CHECKING:
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell


# Probit scale for ``E[σ(N(m, v))] ≈ σ(m / √(1 + π·v/8))``.
_PROBIT_SCALE = math.pi / 8.0


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _binary_entropy(p: float) -> float:
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
    return logistic((mu_c - delta_s) / math.sqrt(1.0 + _PROBIT_SCALE * v))


def update_theta_posterior(
    mu: float, var: float, delta_s: float, se_delta_s: float, hit: bool
) -> tuple[float, float]:
    """One Newton step on a 1PL likelihood marginalized over δ_s via probit, not a full fit."""
    scale = math.sqrt(1.0 + _PROBIT_SCALE * se_delta_s * se_delta_s)
    p = logistic((mu - delta_s) / scale)
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
    m = mu_c - delta_s
    v = var_c + se_delta_s * se_delta_s
    p_bar = logistic(m / math.sqrt(1.0 + _PROBIT_SCALE * v))
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


def parent_grades(parent_rows: Iterable[GradedCell]) -> dict[str, float]:
    """CORRECTNESS, not the composite: ``is_hit`` reads every sub-1.0 ``per_cell`` composite as a miss."""
    return {cell.key: ROW_GRADES["fitness"].read(cell) for cell in parent_rows if cell.scored}


def stratum_shares(sizes: list[int], budget: int) -> list[int]:
    """Equal shares of *budget*; a stratum short of its share hands the rest to the others."""
    shares = [0] * len(sizes)
    left = budget
    open_ = [i for i, n in enumerate(sizes) if n > 0]
    while left > 0 and open_:
        each, extra = divmod(left, len(open_))
        for j, i in enumerate(open_):
            take = min(sizes[i] - shares[i], each + (j < extra))
            shares[i] += take
            left -= take
        open_ = [i for i in open_ if shares[i] < sizes[i]]
    return shares


def covered_prefix(ranked: Sequence[Sample], budget: int) -> list[Sample]:
    """*ranked* led by one cell per shared ``source_pin`` level, so no draw narrows to one dataset or one model."""
    held: dict[str, set[str]] = {}
    for s in ranked:
        for key, value in (s.source_pin or {}).items():
            held.setdefault(f"{key}={value!r}", set()).add(s.key)
    shared = {level for level, keys in held.items() if 1 < len(keys) < len(ranked)}
    cover: list[Sample] = []
    for s in ranked:
        levels = {level for level in shared if s.key in held[level]}
        if len(cover) < budget and levels:
            cover.append(s)
            shared -= levels
    taken = {s.key for s in cover}
    return [*cover, *(s for s in ranked if s.key not in taken)][:budget]
