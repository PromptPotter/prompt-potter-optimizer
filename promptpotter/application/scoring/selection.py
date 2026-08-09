"""Which candidate WINS, and which is cut — the θ-ranked election plus the closed-form elimination
posterior, and the paired-cell substrate both stand on. ONE rule, two callers: the live scorer
(``l1_score``) and the resume divergence replayer, so a resumed run can never re-elect a different
winner under an unchanged scorer.

``composite_ci`` lives here rather than with the fitness gateway because it reads
``_mean_fitness_by_cell``, whose docstring forbids adding the scoreable filter that
``elect_round_winner`` relies on being absent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.shared.errors import is_error_result

if TYPE_CHECKING:
    from promptpotter.application.intelligence.exploration import RaschPosterior, Ruler
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "composite_ci",
    "elect_round_winner",
    "elimination_p_best",
    "paired_fitness",
]


def composite_ci(results: list[QueryMeasurement]) -> tuple[float | None, float | None]:
    """Brackets the SCOREABLE population, since that is the number it is drawn beside — but do
    not push that filter into ``_mean_fitness_by_cell``, which the overlap guard needs un-predicated."""
    # Lazy: scoring → optimization circular.
    from promptpotter.application.optimization.pobb.classification import is_deprecated
    from promptpotter.shared.statistics import mean_ci

    scoreable = [r for r in results if not is_deprecated(r) and not is_error_result(r)]
    per_cell = list(_mean_fitness_by_cell(scoreable).values())
    if not per_cell:
        return (None, None)
    _, ci_lo, ci_hi = mean_ci(per_cell)
    # Clipped to the metric's own support. ``mean_ci`` is a normal-CLT band carrying PoBB's
    # ``1/(4n)`` SE floor, so a candidate whose cells all scored 0.0 came out at ±0.0817 on six
    # samples — an interval claiming negative accuracy, which the webapp then clamped at paint
    # time to keep the whisker inside its own axis. The band stays deliberately optimistic at
    # the boundary (it is the posterior PoBB eliminated on, drawn as the loop believed it), but
    # it may not claim support the quantity does not have.
    return (min(max(ci_lo, 0.0), 1.0), min(max(ci_hi, 0.0), 1.0))


# ---------------------------------------------------------------------------
# Round-winner election — difficulty-adjusted ability (θ) ranking shared by the
# live scorer (``l1_score``) and the resume divergence replayer. ONE rule, two
# callers: a resumed run can never re-elect a different winner under an unchanged
# scorer. ``paired_fitness`` remains the origin-overlap guard + the recorded
# p_value diagnostic in ``l1_score``; the *ranking* is θ, not its mean.
# ---------------------------------------------------------------------------


def _mean_fitness_by_cell(rows: list[QueryMeasurement]) -> dict[Any, float]:
    """Un-predicated ON PURPOSE — this is the origin-overlap population, not the display one, and
    ``elect_round_winner`` relies on an errored row counting as a 0.0 cell. Do not add the filter."""
    acc: dict[Any, list[float]] = {}
    for r in rows:
        sid = r.get("sample_id")
        if sid is not None:
            acc.setdefault(sid, []).append(float(r.get("fitness", 0.0) or 0.0))
    return {sid: sum(v) / len(v) for sid, v in acc.items()}


def _distinct_valid_cells(results: list[QueryMeasurement]) -> int:
    """Counted per CELL, not per row, so replicate rows can never falsely satisfy
    ``coverage_floor``; a cell with one errored and one clean row still counts."""
    from promptpotter.application.optimization.pobb.classification import is_deprecated

    return len(
        {
            r.get("sample_id")
            for r in results
            if not is_deprecated(r) and not is_error_result(r) and r.get("sample_id") is not None
        }
    )


def paired_fitness(
    candidate_results: list[QueryMeasurement],
    origin_results: list[QueryMeasurement],
) -> tuple[list[float], list[float]]:
    """The matched pairs the round-significance test runs on. Sorted by ``sample_id`` so a replay
    is deterministic."""
    cand_by_sid = _mean_fitness_by_cell(candidate_results)
    origin_by_sid = _mean_fitness_by_cell(origin_results)
    cand_fit: list[float] = []
    origin_fit: list[float] = []
    for sid in sorted(cand_by_sid.keys() & origin_by_sid.keys(), key=lambda s: (s is None, s)):
        cand_fit.append(cand_by_sid[sid])
        origin_fit.append(origin_by_sid[sid])
    return cand_fit, origin_fit


def elect_round_winner(
    candidate_ids: list[str],
    results_by_id: Mapping[str, list[QueryMeasurement]],
    origin_results: list[QueryMeasurement],
    coverage_floor: int,
    delta_scale: Ruler,
) -> tuple[str, RaschPosterior]:
    """The rank key is the POINT-ESTIMATE lift, with no winner's-curse margin — under-probing is ``coverage_floor``'s job.
    The overlap guard and the θ-lift guard cover different holes: one grades an errored row 0.0, the fit drops it."""
    from promptpotter.application.intelligence.exploration import (
        candidate_abilities,
        theta_lift_over_origin,
    )

    abilities = candidate_abilities(
        {cid: list(results_by_id.get(cid) or []) for cid in candidate_ids},
        origin_results,
        delta_scale,
    )

    best_rank: tuple[float, int] = (0.0, 0)
    winner_id = ""
    for cid in candidate_ids:
        cand_results = list(results_by_id.get(cid) or [])
        n_cells = _distinct_valid_cells(cand_results)
        if n_cells < coverage_floor:
            continue
        cand_fit, _ = paired_fitness(cand_results, origin_results)
        if not cand_fit:
            continue
        # Rank by the difficulty-adjusted ability lift POINT ESTIMATE — a candidate strictly
        # above origin wins, no winner's-curse SE margin required. The prior `- theta_se` shrink
        # discarded genuinely-better candidates whenever the posterior was wide (thin per-round
        # budgets ⇒ theta_se can dwarf a real gain), so the loop never compounded a discovered
        # improvement — the next round re-explored origin instead of building on the better
        # candidate. Under-probing is already guarded independently by `coverage_floor` above
        # (a candidate below it never reaches here), so dropping the SE shrink cannot let a thin
        # fluke win — only fully-probed candidates compete, best point-estimate θ takes it.
        # The floor counts EVIDENCE cells (non-errored, the θ fit's own population), not
        # attempted cells — that is what licenses the no-SE-margin rank.
        # `None` = this candidate or the origin was never fit; there is no lift to rank on.
        lift = theta_lift_over_origin(abilities, cid)
        if lift is None:
            continue
        rank = (lift, n_cells)
        if rank > best_rank:
            best_rank = rank
            winner_id = cid
    return winner_id, abilities


def elimination_p_best(
    candidate_grades: Sequence[float],
    paired_prior_grades: Mapping[str, Sequence[float]],
    candidate_sample_ids: Sequence[int],
    delta_scale: Ruler,
) -> tuple[float, dict[str, float]]:
    """Scores on the SAME θ the round-winner election ranks by, so elimination and election cannot
    disagree on what better means. Closed-form, so the resume replayer re-derives the cut exactly."""
    if not paired_prior_grades:
        return 1.0, {}

    import math

    from scipy.stats import norm

    from promptpotter.application.intelligence.exploration import Observation, fit_theta_given_delta

    sids = [int(s) for s in candidate_sample_ids]
    cand_obs = [
        Observation("__cand__", sid, float(g))
        for sid, g in zip(sids, candidate_grades, strict=True)
    ]
    theta_c, se_c = fit_theta_given_delta(cand_obs, delta_scale).get("__cand__", (0.0, 0.0))

    per_prior: dict[str, float] = {}
    for pid, grades in paired_prior_grades.items():
        prior_obs = [Observation(pid, sid, float(g)) for sid, g in zip(sids, grades, strict=True)]
        theta_p, se_p = fit_theta_given_delta(prior_obs, delta_scale).get(pid, (0.0, 0.0))
        denom = math.sqrt(se_c * se_c + se_p * se_p)
        if denom > 1e-12:
            per_prior[pid] = float(norm.cdf((theta_c - theta_p) / denom))
        else:
            per_prior[pid] = 1.0 if theta_c > theta_p else 0.0
    return min(per_prior.values()), per_prior
