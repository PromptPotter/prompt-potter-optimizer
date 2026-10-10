from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from functools import cache
from statistics import NormalDist
from typing import Literal

from promptpotter.shared.hashing import shapes_optimizer_prompt


@shapes_optimizer_prompt
def _beta_fraction(a: float, b: float, x: float) -> float:
    """The continued fraction of the incomplete beta function, by modified Lentz."""
    tiny = 1e-300
    c = 1.0
    d = 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        for num in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1.0 + num * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + num / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-16:
            break
    return h


@shapes_optimizer_prompt
def _beta_cdf(x: float, a: float, b: float, *, rest: float | None = None) -> float:
    """*rest* is ``1 - x`` where the caller holds it exactly: near 1 the subtraction loses it."""
    rest = 1.0 - x if rest is None else rest
    if x <= 0.0:
        return 0.0
    if rest <= 0.0:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(rest)
    )
    # The fraction converges fast on one side of the mean only; the other side is the mirror.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, rest) / b


@shapes_optimizer_prompt
def _t_sf(x: float, df: float) -> float:
    scale = df + x * x
    tail = 0.5 * _beta_cdf(df / scale, df / 2.0, 0.5, rest=x * x / scale)
    return tail if x >= 0.0 else 1.0 - tail


@shapes_optimizer_prompt
def _t_ppf(p: float, df: float) -> float:
    """Valid for ``p >= 0.5`` only."""
    target = 1.0 - p
    lo, hi = 0.0, 1.0
    while _t_sf(hi, df) > target:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if _t_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-15 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


@shapes_optimizer_prompt
@cache
def t_critical(df: int, alpha: float = 0.05) -> float:
    """Student-t, never the normal quantile, which understates at paired panel sizes."""
    if df < 1:
        raise ValueError(f"t_critical: df must be >= 1, got {df}")
    return _t_ppf(1 - alpha / 2, df)


@shapes_optimizer_prompt
def min_detectable_effect(se: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Takes the estimator's SE, never ``n``: an ``n`` form assumes the binomial worst case."""
    if se <= 0.0:
        return 0.0
    normal = NormalDist()
    return (normal.inv_cdf(1 - alpha / 2) + normal.inv_cdf(power)) * se


def p_exceeds(mean_a: float, se_a: float, mean_b: float, se_b: float) -> float:
    """Divides by the noise of the DIFFERENCE, never by either side's own SE."""
    denom = math.sqrt(se_a * se_a + se_b * se_b)
    if denom <= 1e-12:
        return 1.0 if mean_a > mean_b else 0.0
    return NormalDist().cdf((mean_a - mean_b) / denom)


@shapes_optimizer_prompt
def _normal_posterior(scores: list[float]) -> tuple[float, float]:
    """The SE is floored at ``1/(4n)``: 4/4 hits has variance 0 and would be a point mass."""
    n = len(scores)
    if n == 0:
        return (0.0, 1.0)

    import numpy as np

    arr = np.asarray(scores, dtype=np.float64)
    mean = float(arr.mean())
    if n == 1:
        return (mean, 0.5)
    variance = float(arr.var(ddof=1))
    se = math.sqrt(variance / n)
    se_floor = 1.0 / (4.0 * n)
    return (mean, max(se, se_floor))


@shapes_optimizer_prompt
def paired_diff_posterior(
    candidate_scores: list[float],
    prior_scores: list[float],
) -> tuple[float, float, int]:
    n = len(candidate_scores)
    if len(prior_scores) != n:
        raise ValueError(
            f"paired_diff_posterior: prior has {len(prior_scores)} scores; candidate has {n}"
        )
    diffs = [c - p for c, p in zip(candidate_scores, prior_scores, strict=True)]
    mean_d, se_d = _normal_posterior(diffs)
    return (mean_d, se_d, n)


def mean_ci(values: list[float], alpha: float = 0.05) -> tuple[float, float, float] | None:
    n = len(values)
    if n < 2:
        return None
    mean, se = _normal_posterior(values)
    half = t_critical(n - 1, alpha) * se
    return (mean, mean - half, mean + half)


@shapes_optimizer_prompt
def paired_mean_t(
    candidate_scores: list[float],
    prior_scores: list[float],
    *,
    tail: Literal["two", "greater"] = "two",
    alpha: float = 0.05,
) -> tuple[float, float | None, float | None, float | None, int]:
    """The bracket is two-sided under BOTH tails: never pair a ``greater`` p with it as a verdict."""
    mean_d, se_d, n = paired_diff_posterior(candidate_scores, prior_scores)
    if n < 2:
        return (mean_d, None, None, None, n)
    half = t_critical(n - 1, alpha) * se_d
    # `_normal_posterior` floors the SE strictly above zero.
    t = mean_d / se_d
    beyond = _t_sf(abs(t), n - 1)
    one_sided = beyond if t >= 0.0 else 1.0 - beyond
    p = 2.0 * beyond if tail == "two" else one_sided
    return (mean_d, mean_d - half, mean_d + half, p, n)


def exact_p_floor(n: int) -> float:
    return 1.0 if n < 1 else min(1.0, 2.0 / 2.0**n)


def p_floor(candidate_scores: Sequence[float], prior_scores: Sequence[float]) -> float:
    return exact_p_floor(sum(discordant_counts(candidate_scores, prior_scores)))


def discordant_counts(candidate: Sequence[float], prior: Sequence[float]) -> tuple[int, int]:
    wins = sum(1 for c, p in zip(candidate, prior, strict=True) if c > p)
    losses = sum(1 for c, p in zip(candidate, prior, strict=True) if c < p)
    return wins, losses


def sign_posterior(wins: int, losses: int) -> float:
    if wins + losses == 0:
        return 0.5
    return 1.0 - _beta_cdf(0.5, wins + 1, losses + 1)


def _signed_rank_counts(n: int) -> list[int]:
    total = n * (n + 1) // 2
    counts = [0] * (total + 1)
    counts[0] = 1
    for rank in range(1, n + 1):
        for w in range(total, rank - 1, -1):
            counts[w] += counts[w - rank]
    return counts


def exact_paired_reading(
    candidate_scores: list[float],
    prior_scores: list[float],
    *,
    alpha: float = 0.05,
) -> tuple[float, float | None, float | None, float | None, int]:
    """Wilcoxon signed-rank; both bracket ends are ``None`` where *alpha* is unreachable at *n*."""
    diffs = [c - p for c, p in zip(candidate_scores, prior_scores, strict=True)]
    nonzero = [d for d in diffs if d != 0.0]
    n = len(nonzero)
    walsh = sorted((a + b) / 2.0 for i, a in enumerate(nonzero) for b in nonzero[i:])
    mid = len(walsh) // 2
    shift = (
        0.0
        if not walsh
        else (walsh[mid] if len(walsh) % 2 else (walsh[mid - 1] + walsh[mid]) / 2.0)
    )
    if n < 2:
        return (shift, None, None, None, n)

    counts = _signed_rank_counts(n)
    draws = float(2**n)
    total = n * (n + 1) // 2
    by_magnitude = sorted(range(n), key=lambda i: abs(nonzero[i]))
    observed = sum(rank for rank, i in enumerate(by_magnitude, start=1) if nonzero[i] > 0.0)
    # Doubled, so a half-integer centre never needs a tolerance to compare against.
    deviation = abs(2 * observed - total)
    p = min(1.0, sum(c for w, c in enumerate(counts) if abs(2 * w - total) >= deviation) / draws)

    # k = the largest lower tail still inside alpha/2; 0 says this width cannot bracket at alpha.
    cumulative = 0
    k = 0
    for w, c in enumerate(counts):
        cumulative += c
        if cumulative / draws > alpha / 2.0:
            break
        k = w + 1
    if k < 1:
        return (shift, None, None, p, n)
    return (shift, walsh[k - 1], walsh[len(walsh) - k], p, n)


def holm_adjusted(p_values: list[float]) -> list[float]:
    """In INPUT order. Holm, not Benjamini-Hochberg: pairs that SHARE arms are dependent."""
    m = len(p_values)
    if m == 0:
        return []

    out = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(sorted(range(m), key=lambda i: p_values[i])):
        running = max(running, (m - rank) * p_values[idx])
        out[idx] = min(1.0, running)
    return out


def two_way_effect_sds(
    cells_by_arm: Mapping[str, Mapping[str, float]],
) -> tuple[float, float, float] | None:
    """``(cell_sd, arm_sd, residual_sd)`` over the cells EVERY arm measured."""
    arms = sorted(cells_by_arm)
    if len(arms) < 2:
        return None
    shared = sorted(set.intersection(*(set(cells_by_arm[a]) for a in arms)))
    if len(shared) < 2:
        return None

    grand = sum(cells_by_arm[a][c] for a in arms for c in shared) / (len(arms) * len(shared))
    arm_mean = {a: sum(cells_by_arm[a][c] for c in shared) / len(shared) for a in arms}
    cell_mean = {c: sum(cells_by_arm[a][c] for a in arms) / len(arms) for c in shared}
    ss = sum(
        (cells_by_arm[a][c] - arm_mean[a] - cell_mean[c] + grand) ** 2 for a in arms for c in shared
    )
    residual = math.sqrt(ss / ((len(arms) - 1) * (len(shared) - 1)))
    return (_sd(list(cell_mean.values())), _sd(list(arm_mean.values())), residual)


@shapes_optimizer_prompt
def _sd(xs: list[float]) -> float:
    mean = sum(xs) / len(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def sample_sd(xs: list[float]) -> float | None:
    return None if len(xs) < 2 else _sd(xs)


def rank_correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


def _average_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def _order(a: float, b: float) -> int:
    return (a > b) - (a < b)


def _rank_agreement(full: Sequence[float], proxy: Sequence[float]) -> float:
    pairs = [(i, j) for i in range(len(full)) for j in range(i + 1, len(full))]
    if not pairs:
        return 1.0
    total = 0.0
    for i, j in pairs:
        f, p = _order(full[i], full[j]), _order(proxy[i], proxy[j])
        # The paper grants a tie on one side "partial credit" without its size; half is the midpoint.
        total += 1.0 if f == p else 0.5 if f == 0 or p == 0 else 0.0
    return total / len(pairs)


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy) if sxx > 0.0 and syy > 0.0 else None


def greedy_column_subset(
    matrix: Sequence[Sequence[float]],
    k: int,
    *,
    rank_weight: float,
    separation_weight: float,
    redundancy_weight: float,
) -> list[int]:
    """Greedy by LEVI's marginal score (arXiv 2605.09764 §3.3) over candidates × examples."""
    n_rows, n_cols = len(matrix), len(matrix[0]) if matrix else 0
    columns = [[matrix[i][j] for i in range(n_rows)] for j in range(n_cols)]
    full = [sum(row) / n_cols for row in matrix]
    spreads = [math.sqrt(sum((v - sum(c) / n_rows) ** 2 for v in c) / n_rows) for c in columns]
    widest = max(spreads, default=0.0)
    chosen: list[int] = []
    sums = [0.0] * n_rows
    while len(chosen) < min(k, n_cols):
        best, best_score = -1, -math.inf
        for j in range(n_cols):
            if j in chosen:
                continue
            taken = [*chosen, j]
            proxy = [(sums[i] + columns[j][i]) / len(taken) for i in range(n_rows)]
            separation = sum(spreads[c] for c in taken) / len(taken) / widest if widest else 0.0
            redundancy = (
                sum(abs(_pearson(columns[j], columns[c]) or 0.0) for c in chosen) / len(chosen)
                if chosen
                else 0.0
            )
            score = (
                rank_weight * _rank_agreement(full, proxy)
                + separation_weight * separation
                - redundancy_weight * redundancy
            )
            if score > best_score:
                best, best_score = j, score
        chosen.append(best)
        sums = [sums[i] + columns[best][i] for i in range(n_rows)]
    return chosen


__all__ = [
    "discordant_counts",
    "exact_p_floor",
    "greedy_column_subset",
    "holm_adjusted",
    "mean_ci",
    "min_detectable_effect",
    "p_floor",
    "paired_diff_posterior",
    "paired_mean_t",
    "rank_correlation",
    "sample_sd",
    "sign_posterior",
    "t_critical",
    "two_way_effect_sds",
]
