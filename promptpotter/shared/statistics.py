"""Pure statistics — leaf-level, depending only on stdlib + numpy + scipy. Requires the ``[stats]`` extra."""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Mapping


def warm_stats_backend() -> None:
    """Import ``scipy.stats`` on a daemon thread so the round loop never pays for it — otherwise the first PoBB check of round 1
    triggers it and reads as a freeze. A missing scipy is swallowed; the real call site raises naming the ``[stats]`` extra."""

    def _warm() -> None:
        with contextlib.suppress(ImportError):
            import scipy.stats  # noqa: F401

    threading.Thread(target=_warm, name="stats-warm", daemon=True).start()


def t_critical(df: int, alpha: float = 0.05) -> float:
    """Two-sided Student-t critical value for a mean whose SE was estimated from the same few observations — the normal
    quantile understates the interval at the panel sizes the paired verdicts run on."""
    if df < 1:
        raise ValueError(f"t_critical: df must be >= 1, got {df}")

    from scipy.stats import t

    return float(t.ppf(1 - alpha / 2, df))


def min_detectable_effect(se: float, alpha: float = 0.05, power: float = 0.8) -> float:
    """Smallest effect detectable given the estimator's OWN standard error — takes the SE, never a sample count. The ``n``-form assumes
    binomial worst case, wrong for every caller here, and overstated the panel's MDE 3.8x."""
    if se <= 0.0:
        return 0.0

    from scipy.stats import norm

    return float((norm.ppf(1 - alpha / 2) + norm.ppf(power)) * se)


# --- PoBB: Posterior-of-Being-Best (Russo 2016 / Top-Two Thompson family) ---


def _normal_posterior(scores: list[float]) -> tuple[float, float]:
    """Normal posterior on the population mean of *scores*. SE is clipped to the Beta-Binomial worst case, which protects the
    small-n binary regime — 4/4 hits has empirical variance 0 and would collapse to a point mass, stopping exploration."""
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


# --- Paired-difference posterior — cand-vs-prior on shared sample set ---


def paired_diff_posterior(
    candidate_scores: list[float],
    prior_scores: list[float],
) -> tuple[float, float, int]:
    """Closed-form paired-difference posterior, for telemetry — the per-prior diff the round audit and the p_value diagnostic
    carry, computed over a shared sample set."""
    n = len(candidate_scores)
    if len(prior_scores) != n:
        raise ValueError(
            f"paired_diff_posterior: prior has {len(prior_scores)} scores; candidate has {n}"
        )
    diffs = [c - p for c, p in zip(candidate_scores, prior_scores, strict=True)]
    mean_d, se_d = _normal_posterior(diffs)
    return (mean_d, se_d, n)


def mean_ci(values: list[float], alpha: float = 0.05) -> tuple[float, float, float]:
    """Normal-CLT interval on the mean of *values* — the same posterior PoBB gets, expressed on the values' own scale rather
    than as a difference. ``n=0`` is degenerate."""
    if not values:
        return (0.0, 0.0, 0.0)

    from scipy.stats import norm

    mean, se = _normal_posterior(values)
    z = norm.ppf(1 - alpha / 2)
    return (mean, mean - z * se, mean + z * se)


def two_way_effect_sds(
    cells_by_arm: Mapping[str, Mapping[str, float]],
) -> tuple[float, float, float] | None:
    """Additive arm + cell decomposition, returning ``(cell_sd, arm_sd, residual_sd)``.

    Restricted to the cells EVERY arm measured, because an arm scored on an easier subset would
    otherwise carry that subset's difficulty as its own effect. ``None`` below two arms or two
    shared cells, where there is nothing to separate.

    The arm SD alone decides nothing: under the null an arm MEAN still scatters by
    ``residual_sd / sqrt(n_cells)``, so an arm SD at or below that is noise, not a ranking.
    """
    arms = sorted(cells_by_arm)
    if len(arms) < 2:
        return None
    shared = sorted(set.intersection(*(set(cells_by_arm[a]) for a in arms)))
    if len(shared) < 2:
        return None

    grand = sum(cells_by_arm[a][c] for a in arms for c in shared) / (len(arms) * len(shared))
    arm_mean = {a: sum(cells_by_arm[a][c] for c in shared) / len(shared) for a in arms}
    cell_mean = {c: sum(cells_by_arm[a][c] for a in arms) / len(arms) for c in shared}
    # df = (arms-1)(cells-1): both margins are estimated from the same table.
    ss = sum(
        (cells_by_arm[a][c] - arm_mean[a] - cell_mean[c] + grand) ** 2 for a in arms for c in shared
    )
    residual = math.sqrt(ss / ((len(arms) - 1) * (len(shared) - 1)))
    return (_sd(list(cell_mean.values())), _sd(list(arm_mean.values())), residual)


def _sd(xs: list[float]) -> float:
    mean = sum(xs) / len(xs)
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def rank_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Spearman ρ. ``None`` below three pairs, or where either side is constant and no ranking
    exists to correlate."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None

    from scipy.stats import spearmanr

    rho = float(spearmanr(xs, ys).statistic)
    return None if math.isnan(rho) else rho


__all__ = [
    "mean_ci",
    "min_detectable_effect",
    "paired_diff_posterior",
    "rank_correlation",
    "t_critical",
    "two_way_effect_sds",
    "warm_stats_backend",
]
