"""Which candidate WINS, and which is cut — the θ-ranked election plus the closed-form elimination
posterior, and the paired-cell substrate both stand on. ONE rule, three callers: the live scorer
(``l1_score``), the resume divergence replayer and the A/B replay, so a resumed run can never
re-elect a different winner under an unchanged scorer.

One rule is not enough on its own — the PARENT has to travel with it. The rule ranks each arm
against ``parent_results``, and a caller passing a different panel re-elects differently with the
scorer untouched. So the live election records the parent it used (``parent_cells``) and both
replayers read it; none of them may reconstruct one.

``mean_fitness_ci`` lives here rather than with the fitness gateway because it reads
``_mean_fitness_by_cell``, whose docstring forbids adding the scoreable filter that
``elect_round_winner`` relies on being absent."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.intelligence.exploration import RaschPosterior
    from promptpotter.domain.results import RoundResult
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.scoring import QueryMeasurement

__all__ = [
    "distinct_valid_cells",
    "elect_round_winner",
    "elimination_p_best",
    "matched_parent_lift",
    "mean_fitness_ci",
    "paired_fitness",
    "parent_cells",
    "parent_selection_bias",
]


def mean_fitness_ci(results: list[QueryMeasurement]) -> tuple[float | None, float | None]:
    """Brackets the scoreable population, since that is the number it is drawn beside. A DECISION
    grades an errored row 0.0 (the arm was asked and produced nothing); an interval drawn beside a
    point estimate must bracket the population that estimate came from — hence the filter here and
    deliberately not inside ``_mean_fitness_by_cell``."""
    # Lazy: scoring → optimization circular.
    from promptpotter.application.optimization.pobb.classification import scoreable_rows
    from promptpotter.shared.statistics import mean_ci

    per_cell = list(_mean_fitness_by_cell(scoreable_rows(results)).values())
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


def distinct_valid_cells(results: list[QueryMeasurement]) -> int:
    """Counted per CELL, not per row, so replicate rows can never falsely satisfy
    ``coverage_floor``; a cell with one errored and one clean row still counts."""
    from promptpotter.application.optimization.pobb.classification import scoreable_rows

    return len({sid for r in scoreable_rows(results) if (sid := r.get("sample_id")) is not None})


def paired_fitness(
    candidate_results: list[QueryMeasurement],
    parent_results: list[QueryMeasurement],
) -> tuple[list[float], list[float]]:
    """The matched pairs the round-significance test runs on. Sorted by ``sample_id`` so a replay
    is deterministic. Every caller pairs against the PARENT — the origin only at round 0."""
    cand_by_sid = _mean_fitness_by_cell(candidate_results)
    parent_by_sid = _mean_fitness_by_cell(parent_results)
    cand_fit: list[float] = []
    parent_fit: list[float] = []
    for sid in sorted(cand_by_sid.keys() & parent_by_sid.keys(), key=lambda s: (s is None, s)):
        cand_fit.append(cand_by_sid[sid])
        parent_fit.append(parent_by_sid[sid])
    return cand_fit, parent_fit


def matched_parent_lift(
    candidate_results: list[QueryMeasurement],
    parent_results: list[QueryMeasurement],
) -> tuple[float, float, float] | None:
    """``(lift, ci_lo, ci_hi)`` against the PARENT ON THE CELLS BOTH MEASURED — the blocked comparison,
    sharper than ``mean_fitness_ci`` on the same rows. The parent is the origin only at round 0 and the
    prior winner after it, which is what the name states. ``None`` below two shared cells."""
    # One pair has no spread, and an interval drawn from it claims a precision nobody bought.
    # ``scoreable_rows`` on both arms, matching ``mean_fitness_ci`` — the two intervals sit on one
    # row and must bracket one population. A cell either arm errored on drops the PAIR, which is what
    # makes the panel a narrowed comparison rather than a smaller one; ``n_cells`` cannot say which.
    from promptpotter.application.optimization.pobb.classification import scoreable_rows
    from promptpotter.shared.statistics import paired_reading

    cand_fit, parent_fit = paired_fitness(
        scoreable_rows(candidate_results), scoreable_rows(parent_results)
    )
    lift, ci_lo, ci_hi, _p, _n = paired_reading(cand_fit, parent_fit)
    return None if ci_lo is None or ci_hi is None else (lift, ci_lo, ci_hi)


def parent_cells(parent_results: list[QueryMeasurement]) -> list[dict[str, Any]]:
    """The parent panel an election ranked against, projected to the fields
    ``elect_round_winner`` reads off it: the cell, whether it errored, and its grade — which is
    ``objective``, not ``fitness``.

    **Both grades, and the pair is not redundancy.** A round is won on θ, which
    ``candidate_abilities`` fits through ``exploration.py::graded_response`` — that reader takes
    ``objective``, RAISES on its absence, and is the one place a cost or latency term reaches the
    election at all; ``fitness`` is what the paired lift beside it reads. ``objective`` is carried
    only where the row has it, so a genuinely ungraded cell still raises rather than reading as a
    miss.

    Recorded beside the decision because nothing else on the round document carries it — on a WON
    round ``RoundResult.results`` holds the winner's rows, not the parent's — so a replayer had to
    reconstruct the parent, and every reconstruction picked a different one than live used."""
    return [
        {
            "sample_id": sid,
            "fitness": r.get("fitness"),
            "error_category": r.get("error_category"),
            **({"objective": r["objective"]} if "objective" in r else {}),
        }
        for r in parent_results
        if (sid := r.get("sample_id")) is not None
    ]


# E[max of k standard normals], k = 1..6. Beyond that the table saturates slowly and the last
# entry is used — a round with seven electable arms is not a shape this loop produces.
_EXPECTED_MAX_Z: tuple[float, ...] = (0.0, 0.0, 0.5642, 0.8463, 1.0294, 1.1630, 1.2672)


def parent_selection_bias(rounds: Sequence[RoundResult]) -> float:
    """How much of the standing parent's θ is the selection that crowned it, in logits.

    A winner is the MAXIMUM over the round's electable arms, so its θ carries that round's largest
    noise draw — and ``rescore_parent`` replays its cached rows, so the inflation never washes out.
    Corrects the BAR only: the challengers are unselected draws and carry no such term."""
    for rr in reversed(rounds):
        if not rr.winner_id:
            continue
        winner = next((c for c in rr.candidate_scores if c.candidate_id == rr.winner_id), None)
        se = winner.theta_se if winner else None
        if not se:
            return 0.0
        k = min(max(rr.electable_count, 1), len(_EXPECTED_MAX_Z) - 1)
        # ONE arm's SE, not the paired √2 one: θ̂_parent is common to all k comparisons, so it
        # shifts every lift equally and is never what the max selects on. A correction that
        # over-corrects buys back exactly the noise-crowning it exists to stop.
        return _EXPECTED_MAX_Z[k] * se
    return 0.0


def elect_round_winner(
    candidate_ids: list[str],
    results_by_id: Mapping[str, list[QueryMeasurement]],
    parent_results: list[QueryMeasurement],
    coverage_floor: int,
    ruler: DeltaRuler | None,
    *,
    parent_bias: float,
) -> tuple[str, RaschPosterior]:
    """ADMISSION is the point-estimate lift — strictly above the parent. The RANK is ``P(θ_cand >
    θ_parent)``, the same quantity ``elimination_p_best`` cuts on, so the two cannot disagree about
    what better means. The overlap guard and the θ-lift guard cover different holes: one grades an
    errored row 0.0, the fit drops it."""
    from promptpotter.application.intelligence.exploration import (
        PARENT_ABILITY_ID,
        candidate_abilities,
        theta_lift_over_parent,
    )
    from promptpotter.shared.statistics import p_exceeds

    abilities = candidate_abilities(
        {cid: list(results_by_id.get(cid) or []) for cid in candidate_ids},
        parent_results,
        ruler,
    )

    theta_parent = abilities.theta.get(PARENT_ABILITY_ID)
    se_parent = abilities.theta_se.get(PARENT_ABILITY_ID) or 0.0
    # The bar is what the parent can DO, not the draw that crowned it (`parent_selection_bias`).
    if theta_parent is not None:
        theta_parent -= parent_bias

    best_rank: tuple[float, int] = (0.0, 0)
    winner_id = ""
    for cid in candidate_ids:
        cand_results = list(results_by_id.get(cid) or [])
        n_cells = distinct_valid_cells(cand_results)
        # Catches an arm thin for a reason OTHER than elimination (an operator skip). PoBB never
        # cuts below its own `n_min`, which IS this floor, so no cut arm is stopped here.
        if n_cells < coverage_floor:
            continue
        cand_fit, _ = paired_fitness(cand_results, parent_results)
        if not cand_fit:
            continue
        # ADMISSION is the bare point lift, no SE margin — subtracting one shrinks the estimate
        # itself, turning a wide-posterior gain negative. Uncertainty belongs in the RANK below.
        lift = theta_lift_over_parent(abilities, cid)
        if lift is None:
            continue
        # The credit is EARNED, not granted: it corrects a bar read at `se_parent`, so an arm read
        # less precisely carries a wider draw of its own and a flat credit would be worth most to
        # the noisiest arm in the round — the one that needs it least.
        se_cand = abilities.theta_se.get(cid) or 0.0
        lift += parent_bias * (min(1.0, se_parent / se_cand) if se_parent and se_cand else 1.0)
        if lift <= 0.0:
            continue
        # RANK: the lift over the noise it cleared, because a bare gap cannot say whether the
        # round could TELL the arms apart — a thin arm out-points a full panel on a margin
        # inside its own SE.
        theta_c = abilities.theta.get(cid)
        if theta_c is None or theta_parent is None:
            continue
        p_better = p_exceeds(theta_c, abilities.theta_se.get(cid) or 0.0, theta_parent, se_parent)
        rank = (p_better, n_cells)
        if rank > best_rank:
            best_rank = rank
            winner_id = cid
    return winner_id, abilities


def elimination_p_best(
    candidate_grades: Sequence[float],
    paired_prior_grades: Mapping[str, Sequence[float]],
    candidate_sample_ids: Sequence[int],
    ruler: DeltaRuler | None,
) -> tuple[float, dict[str, float]]:
    """Scores on the SAME θ the round-winner election ranks by, so elimination and election cannot
    disagree on what better means. Closed-form, so the resume replayer re-derives the cut exactly."""
    if not paired_prior_grades:
        return 1.0, {}

    from promptpotter.application.intelligence.exploration import Observation, fit_theta_given_delta
    from promptpotter.shared.statistics import p_exceeds

    sids = [int(s) for s in candidate_sample_ids]
    # The ONE sanctioned provisional read: this runs DURING the round, on cells `calibrate_ruler`
    # cannot have absorbed yet — extension needs the round's grades, which do not exist while the
    # round is still buying them. Misses stand at the ruler's own centre, and since both arms are
    # scored on the identical cell list they see the identical δ vector, so a constant
    # misspecification cannot favour one of them.
    entries = ruler.entries_covering(sids) if ruler is not None else None
    anchor = ruler.anchor_id if ruler is not None else ""
    cand_obs = [
        Observation("__cand__", sid, float(g))
        for sid, g in zip(sids, candidate_grades, strict=True)
    ]
    theta_c, se_c = fit_theta_given_delta(cand_obs, entries, anchor_id=anchor).get(
        "__cand__", (0.0, 0.0)
    )

    per_prior: dict[str, float] = {}
    for pid, grades in paired_prior_grades.items():
        prior_obs = [Observation(pid, sid, float(g)) for sid, g in zip(sids, grades, strict=True)]
        theta_p, se_p = fit_theta_given_delta(prior_obs, entries, anchor_id=anchor).get(
            pid, (0.0, 0.0)
        )
        # The SAME comparison `elect_round_winner` ranks on — one function, so a cut and a crown
        # cannot be computed on two different readings of "better".
        per_prior[pid] = p_exceeds(theta_c, se_c, theta_p, se_p)
    return min(per_prior.values()), per_prior
