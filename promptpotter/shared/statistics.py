"""Pure statistics — leaf-level, depending only on stdlib + numpy + scipy. Requires the ``[stats]`` extra."""

from __future__ import annotations

import contextlib
import math
import threading
from collections.abc import Mapping
from typing import Literal


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


def p_exceeds(mean_a: float, se_a: float, mean_b: float, se_b: float) -> float:
    """``P(a > b)`` under independent normal posteriors — the point gap over the noise of the
    DIFFERENCE, which is the noise a move actually has to clear.

    What a RANKING wants wherever a raw gap misleads: a wide posterior cannot buy a place with a
    margin it never measured, yet nothing is disqualified, since any positive gap stays above 0.5.
    That is what separates it from subtracting an SE, which can turn a real gain negative."""
    denom = math.sqrt(se_a * se_a + se_b * se_b)
    if denom <= 1e-12:
        return 1.0 if mean_a > mean_b else 0.0

    from scipy.stats import norm

    return float(norm.cdf((mean_a - mean_b) / denom))


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


def mean_ci_t(values: list[float], alpha: float = 0.05) -> tuple[float, float, float, int] | None:
    """``(mean, lo, hi, n)`` on the SAME posterior as :func:`mean_ci`, bracketed with Student-t instead of the normal quantile.

    Not a second spelling of ``mean_ci``, which stays z because it is pinned to the persisted ``noise_floor_ci_*`` fields. This
    is for a READ that brackets a handful of cells, where the two quantiles are not interchangeable: at 6 cells t is 2.571
    against z's 1.96, so the normal understates the interval by a third. It is the bracket ``matched_parent_lift`` and the
    edit ranking already use, so a campaign interval and a paired difference beside it cannot disagree about zero.

    ``None`` below two values: one reading has no spread, and a bracket drawn from it is a fiction."""
    n = len(values)
    if n < 2:
        return None

    mean, se = _normal_posterior(values)
    half = t_critical(n - 1, alpha) * se
    return (mean, mean - half, mean + half, n)


def paired_reading(
    candidate_scores: list[float],
    prior_scores: list[float],
    *,
    tail: Literal["two", "greater"] = "two",
    alpha: float = 0.05,
) -> tuple[float, float | None, float | None, float | None, int]:
    """``(mean_d, ci_lo, ci_hi, p, n)`` over one paired difference — the bracket and the test from ONE posterior, so a caller
    cannot draw an interval and a p-value that disagree about zero.

    ``two`` asks whether two arms differ, with no pre-registered direction, so reading a pair in either order gives one number;
    ``greater`` asks whether the candidate BEAT the prior, which is directional on purpose. **The bracket is two-sided under
    both**, so those agree about zero only under ``two`` — a document pairing a ``greater`` p with this interval reports two
    different tests as one verdict, and in the band between the tails they disagree outright. Take both from one call, or
    take the p alone.

    Below two pairs the bracket and the test are ``None``: nothing was tested, and a ``1.0`` there would misreport that as a
    test that found nothing. The SE is :func:`paired_diff_posterior`'s, floored at ``1/(4n)``, so ``p`` is CONSERVATIVE wherever
    the observed spread falls below that floor."""
    mean_d, se_d, n = paired_diff_posterior(candidate_scores, prior_scores)
    if n < 2:
        return (mean_d, None, None, None, n)

    from scipy.stats import t

    # `_normal_posterior` floors the SE strictly above zero, so there is no degenerate branch here.
    half = t_critical(n - 1, alpha) * se_d
    upper = float(t.sf(mean_d / se_d, n - 1))
    return (
        mean_d,
        mean_d - half,
        mean_d + half,
        2.0 * min(upper, 1.0 - upper) if tail == "two" else upper,
        n,
    )


def exact_p_floor(n: int) -> float:
    """The smallest two-sided p an exact paired test on *n* pairs can reach — the two sign draws
    where every difference agrees. A fact about the panel's WIDTH, never about the edit under test:
    below it no verdict exists at ANY effect size."""
    return 1.0 if n < 1 else min(1.0, 2.0 / 2.0**n)


def cells_for_exact_verdict(n_tests: int, alpha: float = 0.05) -> int:
    """Smallest paired *n* whose :func:`exact_p_floor` clears Holm's tightest step over *n_tests*
    comparisons — what to BUY, in the unit a panel is bought in. Holm's first step is its
    strictest, so a width clearing that one can in principle clear every later one."""
    target = alpha / max(1, n_tests)
    n = 1
    while exact_p_floor(n) > target:
        n += 1
    return n


def _signed_rank_counts(n: int) -> list[int]:
    """``out[w]`` = how many of the ``2**n`` sign draws put ``W+`` at *w*. Built by DP, so the null
    costs O(n**3) rather than the ``2**n`` an enumeration would spend."""
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
    """``(median_shift, ci_lo, ci_hi, p, n)`` — the Wilcoxon signed-rank twin of
    :func:`paired_reading`, assuming no distribution, for a read over a handful of cells.

    Estimate, bracket and test come off one set of Walsh averages, so they cannot disagree about
    zero. ``p`` never falls below :func:`exact_p_floor` and both bracket ends are ``None`` where
    *alpha* is unreachable at this *n* — a verdict the width cannot support is refused, never
    borrowed from an unmeasured tail."""
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

    # The k-th Walsh average in from each end, k being the largest lower tail still inside alpha/2.
    # k = 0 says this width cannot bracket at alpha at all.
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
    """Holm-Bonferroni step-down, returned IN INPUT ORDER with monotonicity enforced and clipped to 1.0.

    An adjusted p, never a reject/keep flag — the caller renders a number rather than a verdict at an alpha nobody chose.
    Holm rather than Bonferroni, which it uniformly dominates; rather than Benjamini-Hochberg, which controls the false
    DISCOVERY rate over many independent hypotheses, where this corrects a handful of pairs that SHARE arms and is therefore
    valid under the dependence those shared arms create.

    This is a reporting correction and reaches nothing in the loop: candidate selection retired Holm for PoBB
    (``docs/research/related-work.md``) and keeps it."""
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
    cell_sd = sample_sd(list(cell_mean.values()))
    arm_sd = sample_sd(list(arm_mean.values()))
    if cell_sd is None or arm_sd is None:  # both margins are guarded >= 2 above
        return None
    return (cell_sd, arm_sd, residual)


def sample_sd(xs: list[float]) -> float | None:
    """Sample SD (n−1). ``None`` below two points — one reading has no spread, and reporting 0.0
    for it would claim perfect precision from a single measurement. The ONE spelling: it was
    written out three times, and only the copy carrying this guard was right."""
    if len(xs) < 2:
        return None
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
    "cells_for_exact_verdict",
    "exact_p_floor",
    "exact_paired_reading",
    "holm_adjusted",
    "mean_ci",
    "mean_ci_t",
    "min_detectable_effect",
    "paired_diff_posterior",
    "paired_reading",
    "rank_correlation",
    "sample_sd",
    "t_critical",
    "two_way_effect_sds",
    "warm_stats_backend",
]
