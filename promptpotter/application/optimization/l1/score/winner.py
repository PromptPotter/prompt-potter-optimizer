"""Round-winner selection — :func:`l1_score` + :func:`round_winner_key`.

``l1_score`` scores the population (via :func:`score_population`), backfills
the opt_sp-aware composite, elects the winner by ``round_winner_key``, and
produces the round's :class:`RoundResult`.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.l1.population import parse_population
from promptpotter.application.optimization.l1.score.loop import score_population
from promptpotter.application.optimization.pobb.elimination import PoBBConfig
from promptpotter.application.optimization.resume_and_fork import (
    ResumeCheckpointKind,
    ResumeCheckpointRecord,
    record_decision,
)
from promptpotter.application.optimization.validators.l1_strict import L1YieldStats
from promptpotter.application.scoring.metrics import (
    _compute_accuracy,
    compute_composite_fitness,
    count_degraded_samples,
    matched_origin_stats,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    CandidateProposal,
    CandidateScore,
    RoundOrigin,
    RoundResult,
)
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.shared.statistics import proportion_test

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample


def round_winner_key(composite_fitness: float | None, accuracy: float) -> tuple[float, float]:
    """Tiebreak key for round-winner selection: composite-first, accuracy-tiebreak.

    Shared by ``l1_score`` (round-time selection over scored candidates) and
    ``pick_round_winner`` (post-hoc selection over ``ScoreEntry``s for the
    SCOREBOARD ``*`` marker). Centralised so the two surfaces cannot drift
    apart — a single PR that changes this rule moves both readers together.

    ``composite_fitness`` falls back to ``accuracy`` when ``None`` so the key
    is well-defined for ``CandidateScore`` rows minted before backfill (path-1
    validation-skip variants carry ``composite_fitness=0.0``, not ``None``,
    but the fallback keeps the helper total over the type signature).
    """
    return (composite_fitness if composite_fitness is not None else accuracy, accuracy)


def is_leader_eligible(cs: CandidateScore) -> bool:
    """Whether *cs* may be elected the round leader.

    Two disjoint disqualifications:

    (a) Escalation-aborted without a PoBB stop — a true mid-run failure
        outside the measured-comparison surface.
    (b) Any candidate whose run halted on a degradation or
        ``scoring_error_abort`` check (``degradation_context`` is
        populated only by those two check names). Their accuracy is
        computed over a partial valid subset and can inflate above
        origin on a 6/20 run — paired PoBB doesn't help because the
        candidate never produced comparable measurements.

    PoBB-elimination is NOT a disqualification — those candidates went
    through fair paired comparison on real samples and stay in the pool.
    """
    if cs.escalation_aborted and not cs.elimination_stopped:
        return False
    return not cs.degradation_context


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list[Sample],
    origin: RoundOrigin,
    *,
    pipeline_params: dict[str, Any] | None = None,
    improvement_threshold: float = 0.01,
    improvement_significance: float = 0.10,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> tuple[RoundResult, OptSearchPoint]:
    """Score candidates and select the round winner (compares fitness, not composite_fitness).

    Returns ``(round_result, winner_osp)``. ``RoundResult`` is the
    persistence shape (dict-typed lists for serialization); ``winner_osp``
    rides alongside for the ``PromptVersion`` tracing emit in the caller.
    """
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    osp_population, merged_pp = parse_population(
        candidates,
        pipeline_params,
        schema,
        forbidden_axes_strict=cycle.config.optimization.forbidden_axes_strict,
    )
    decisions: list[ResumeCheckpointRecord] = []
    all_candidate_results, candidate_scores, escalation_signal = await score_population(
        cycle,
        osp_population,
        merged_pp,
        candidates,
        dataset,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        pobb_config=pobb_config,
        round_num=round_num,
        decisions=decisions,
        l1_diversity=yield_stats.l1_yield,
    )

    aborted_ids = {cs.candidate_id for cs in candidate_scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    # Origin's full-set stats — the no-candidate-wins fallback for the
    # ``matched_origin`` quad below (every candidate ran every sample ⇒ matched
    # collapses to full).
    origin_base = _compute_accuracy(cast("list[QueryMeasurement]", origin.results))
    best_acc = origin.accuracy
    best_comp = origin.composite_fitness
    best_osp: OptSearchPoint = origin.osp
    best_results: list[QueryMeasurement] = list(cast("list[QueryMeasurement]", origin.results))
    best_label = origin.label
    best_scores: dict[str, float] = dict(origin.evaluators)
    best_matched_origin_acc = origin.accuracy
    best_matched_origin_hits = origin_base["hits"]
    best_matched_origin_composite = origin.composite_fitness
    best_key = round_winner_key(origin.composite_fitness, origin.accuracy)
    winner_idx: int | None = None
    # ``score_population`` populated each ``CandidateScore`` with the
    # ``opt_sp=None`` composite (per-sample scoring path is target-layer; it
    # doesn't see ``OptSearchPoint``). That makes opt_sp-aware evaluators
    # (currently ``prompt_compactness``) collapse to their vacuous fallback,
    # inflating the per-candidate composite shown inline during scoring
    # relative to the post-backfill value that lands on the SCOREBOARD.
    # Index by candidate id so we can back-fill the opt_sp-aware composite +
    # evaluators in one pass before selecting the winner.
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(candidate_scores)}
    for idx, ind in enumerate(scored):
        cand_results = all_candidate_results[ind.lineage.id]
        s = compute_composite_fitness(
            cand_results,
            schema,
            opt_sp=ind,
            round_scorer=session.scoring.round_scorer,
            l1_diversity=yield_stats.l1_yield,
        )
        # Matched-pair origin: PoBB-locked candidates may have only run the
        # hardest q8/20; comparing their 8-sample accuracy to origin's full-set
        # 20-sample accuracy systematically punishes the early-stop optimization.
        # Restricting origin to the candidate's measured samples is the same
        # comparison PoBB's posterior used to declare the lock.
        matched = matched_origin_stats(
            cast("list[QueryMeasurement]", origin.results),
            cand_results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is not None:
            evaluators = {**(s.get("evaluators") or {}), "l1_diversity": yield_stats.l1_yield}
            candidate_scores[cs_idx] = replace(
                candidate_scores[cs_idx],
                composite_fitness=s["composite_fitness"],
                evaluators=evaluators,
                matched_origin_accuracy=matched["accuracy"],
                matched_origin_hits=matched["hits"],
                matched_origin_composite=matched["composite_fitness"],
            )
        # Winner = running max by (composite_fitness, accuracy). Same key as
        # ``round_winner_key`` (used by the SCOREBOARD's ``pick_round_winner``)
        # so the two surfaces report the same winner. The matched-origin gate
        # is applied after selection via ``delta_ok`` below — it decides
        # whether the round is ``improved``, not who won.
        cand_key = round_winner_key(s["composite_fitness"], s["accuracy"])
        if cand_key > best_key:
            best_key = cand_key
            best_acc = s["accuracy"]
            best_comp = s["composite_fitness"]
            best_osp = ind
            best_results = list(cand_results)
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_scores = dict(s.get("evaluators") or {})
            best_matched_origin_acc = matched["accuracy"]
            best_matched_origin_hits = matched["hits"]
            best_matched_origin_composite = matched["composite_fitness"]
            winner_idx = idx

    winner_id = scored[winner_idx].lineage.id if winner_idx is not None and scored else ""
    record_decision(
        decisions,
        ResumeCheckpointKind.ROUND_WINNER,
        {
            "candidate_ids": [ind.lineage.id for ind in scored],
            "round_num": round_num,
        },
        winner_id,
        data={"current_best_accuracy_at_record": origin.accuracy},
        round=round_num,
    )

    base = _compute_accuracy(best_results)
    p_value: float | None = None
    if base["total"] > 0:
        # Real matched-pair hits from the same sample subset the candidate ran.
        # The prior ``round(origin.accuracy * base["total"])`` faked this by
        # extrapolating origin's full-set rate onto the candidate's subset —
        # exactly wrong for hard-first sampling where origin underperforms
        # its full-set rate on the early samples.
        p_value = proportion_test(
            base["hits"], base["total"], best_matched_origin_hits, base["total"]
        )

    delta_ok = best_acc > best_matched_origin_acc + improvement_threshold
    n_min = pobb_config.n_min
    n_ok = base["total"] >= n_min
    sig_ok = improvement_significance >= 1.0 or (
        p_value is not None and p_value < improvement_significance
    )
    improved = delta_ok and n_ok and sig_ok
    improved_reason: str | None = None
    if delta_ok and not improved:
        reasons: list[str] = []
        if not n_ok:
            reasons.append(f"n={base['total']} < elimination_n_min={n_min}")
        if not sig_ok:
            p_repr = f"{p_value:.3f}" if p_value is not None else "None"
            reasons.append(f"p={p_repr} >= {improvement_significance:.2f}")
        improved_reason = "; ".join(reasons)
    round_result = RoundResult(
        round=round_num,
        label=best_label,
        accuracy=best_acc,
        composite_fitness=best_comp,
        hits=base["hits"],
        total=base["total"],
        improved=improved,
        p_value=p_value,
        improved_reason=improved_reason,
        origin_accuracy=origin.accuracy,
        matched_origin_accuracy=best_matched_origin_acc,
        matched_origin_hits=best_matched_origin_hits,
        matched_origin_composite=best_matched_origin_composite,
        prompt_fields={
            **best_osp.prompt_field_dict(),
            "lineage": best_osp.lineage.model_dump(),
        },
        pipeline_params=merged_pp[winner_idx] if winner_idx is not None else pipeline_params,
        results=best_results,
        all_candidate_results=dict(all_candidate_results),
        candidates_scored=len(scored),
        candidate_scores=[cs.to_dict() for cs in candidate_scores],
        decisions=[d.to_dict() for d in decisions],
        degraded_samples=count_degraded_samples(best_results),
        deprecated=base["deprecated"],
        escalation_signal=escalation_signal,
        evaluators=best_scores,
        l1_yield=yield_stats.l1_yield,
        l1_n_no_op=yield_stats.l1_n_no_op,
        l1_n_duplicate=yield_stats.l1_n_duplicate,
    )
    return round_result, best_osp


__all__ = ["is_leader_eligible", "l1_score", "round_winner_key"]
