"""Both terms of the acquisition score and the round order: ``docs/methods/verdict-resolution.md``."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, NamedTuple

from promptpotter.domain.scoring import ROW_GRADES, is_hit
from promptpotter.shared.statistics import logistic

if TYPE_CHECKING:
    from promptpotter.domain.ruler import DeltaRuler
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


def build_round_order(
    parent_grades: Mapping[str, float],
    ruler: DeltaRuler | None,
    cells: Mapping[int, str],
) -> list[int]:
    """*cells* maps each slot on the panel to its ``Sample.key``: grades and δ read by key, ties break by slot."""
    # `None` (never answered) has its own stratum: read as a miss, an unshared panel leads with its easiest cell.
    # An unmeasured δ stands at the ruler's CENTRE, never 0.0, which is a position on this scale.
    miss_stratum: list[int] = []
    hit_stratum: list[int] = []
    unknown_stratum: list[int] = []
    for sid, key in cells.items():
        grade = parent_grades.get(key)
        if grade is None:
            unknown_stratum.append(sid)
        else:
            (hit_stratum if is_hit(grade) else miss_stratum).append(sid)

    centre = ruler.mu_delta if ruler is not None else 0.0
    known = ruler.delta if ruler is not None else {}

    def _delta(sid: int) -> float:
        return known.get(cells[sid], centre)

    miss_stratum.sort(key=lambda sid: (_delta(sid), sid))
    hit_stratum.sort(key=lambda sid: (-_delta(sid), sid))
    # No evidence either way: a cell discriminates most where its δ sits nearest the centre.
    unknown_stratum.sort(key=lambda sid: (abs(_delta(sid) - centre), sid))

    order: list[int] = []
    mi, hi, ui = 0, 0, 0
    for pos in range(1, len(cells) + 1):
        take_hit = pos % 4 == 0
        if (
            take_hit
            and hi < len(hit_stratum)
            and (mi < len(miss_stratum) or ui < len(unknown_stratum))
        ):
            order.append(hit_stratum[hi])
            hi += 1
        elif mi < len(miss_stratum):
            order.append(miss_stratum[mi])
            mi += 1
        elif ui < len(unknown_stratum):
            order.append(unknown_stratum[ui])
            ui += 1
        else:
            order.append(hit_stratum[hi])
            hi += 1
    if sorted(order) != sorted(cells):
        raise ValueError("build_round_order: the order is not a permutation of the panel")
    return order


class CellReading(NamedTuple):
    """What one arm's row says a cell reads and costs, before any other arm is bought on it."""

    level_se: float
    # The share of the cell still open to a lift; a saturated cell reads sharply and decides nothing.
    headroom: float
    usd: float
    seconds: float


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


def information_per_price(readings: Mapping[int, CellReading]) -> dict[int, float]:
    """headroom/(se²·price) over the MEDIAN, so an unreported cell stands at 1.0; price is dollars PLUS clock, each over the pool's median."""
    priced = {
        sid: r for sid, r in readings.items() if r.level_se > 0 and r.usd > 0 and r.seconds > 0
    }
    if not priced:
        return {}
    usd = statistics.median(r.usd for r in priced.values())
    seconds = statistics.median(r.seconds for r in priced.values())
    raw = {
        sid: r.headroom / (r.level_se**2 * (r.usd / usd + r.seconds / seconds))
        for sid, r in priced.items()
    }
    open_cells = [value for value in raw.values() if value > 0]
    if not open_cells:
        return {}
    median = statistics.median(open_cells)
    return {sid: value / median for sid, value in raw.items()}


def decision_order(
    mu_c: float,
    var_c: float,
    mu_s: float,
    var_s: float,
    delta_map: dict[int, float],
    delta_se_map: dict[int, float],
    sample_ids: Iterable[int],
    worth: Mapping[int, float],
) -> list[int]:
    """On ``decision_information_gain`` ALONE: summed, ``delta_learning_gain`` dominates; it overstates ONE arm's draw."""
    return sorted(
        sample_ids,
        key=lambda sid: (
            -decision_information_gain(mu_c, var_c, mu_s, var_s, delta_map[sid], delta_se_map[sid])
            * worth.get(sid, 1.0),
            sid,
        ),
    )
