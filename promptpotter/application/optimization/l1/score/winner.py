"""Round-winner selection. `l1_score` scores via `score_population`, adds the matched-origin
floor, elects by `round_winner_key`, produces `RoundResult`. Composite + evaluators are
opt_sp-aware from the scoring gateway (no recompute here).
"""

from __future__ import annotations

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
from promptpotter.application.origin import rescore_origin
from promptpotter.application.scoring.metrics import (
    _compute_accuracy,
    count_degraded_samples,
    matched_origin_stats,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.rendering import round_winner_key as round_winner_key
from promptpotter.domain.results import (
    CandidateProposal,
    RoundResult,
    ScoredCandidate,
)
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.shared.statistics import paired_diff_posterior

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample


def _paired_fitness(
    candidate_results: list[QueryMeasurement],
    origin_results: list[QueryMeasurement],
) -> tuple[list[float], list[float]]:
    """Per-sample reciprocal-rank fitness for the candidate and origin on the SAME samples,
    aligned by ``sample_id``. The matched pairs the round-significance test runs on — origin's
    fitness restricted to whatever subset the online picker scored the candidate on. A degraded
    sample with no recorded fitness contributes 0 (the score it earned), not a dropped pair.
    """
    origin_by_sid = {
        r.get("sample_id"): float(r.get("fitness", 0.0) or 0.0) for r in origin_results
    }
    cand_fit: list[float] = []
    origin_fit: list[float] = []
    for r in candidate_results:
        sid = r.get("sample_id")
        if sid in origin_by_sid:
            cand_fit.append(float(r.get("fitness", 0.0) or 0.0))
            origin_fit.append(origin_by_sid[sid])
    return cand_fit, origin_fit


def is_leader_eligible(cs: ScoredCandidate) -> bool:
    """Eligibility for round-leader election. Disqualifies (a) escalation-aborted-without-PoBB
    (mid-run failure outside measured-comparison), (b) degradation/scoring_error_abort
    (partial subset accuracy can fake-inflate above origin), and (c) a true PoBB *loss* — the
    eliminator's own verdict that this candidate is not the round's best. A LEADER_LOCKED stop
    (the candidate that locked the lead) is the opposite verdict and stays eligible.
    """
    if cs.escalation_aborted and not cs.elimination_stopped:
        return False
    if cs.degradation_context:
        return False
    ec = cs.elimination_context
    # elim_context is populated only for the "elimination" check, carrying the candidate's own
    # P(best) and epsilon; a loss is p_best < epsilon with the lead NOT locked. Honoring it stops
    # a PoBB-eliminated candidate (p_best below epsilon on a thin subset) from winning the round.
    return not (ec and not ec["leader_locked"] and ec["p_best"] < ec["epsilon"])


def _paired_delta_lcb(
    candidate_results: list[QueryMeasurement],
    origin_results: list[QueryMeasurement],
) -> tuple[float, float]:
    """One-sigma lower-confidence bound of the candidate's per-sample fitness lift over the
    matched origin — the same paired posterior PoBB elimination runs. Returns ``(mean, lcb)``
    with ``lcb = mean - se``: an under-probed candidate has a wide posterior (large ``se``), so
    at equal mean it ranks below a fully-probed one — a lucky 6-sample run can't outrank a
    full-20 candidate on a thin subset mean.
    """
    cand_fit, origin_fit = _paired_fitness(candidate_results, origin_results)
    if not cand_fit:
        return 0.0, 0.0
    mean_d, se_d, _ = paired_diff_posterior(cand_fit, origin_fit)
    return mean_d, mean_d - se_d


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list[Sample],
    *,
    pipeline_params: dict[str, Any] | None = None,
    improvement_threshold: float = 0.01,
    improvement_significance: float = 0.10,
    round_significance_gate: bool = False,
    online_reorder: bool = True,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> tuple[RoundResult, OptSearchPoint]:
    """Score + select winner. Returns `(RoundResult, winner_osp)` — RoundResult is persistence
    shape, winner_osp rides alongside for the caller's `PromptVersion` tracing emit.
    """
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    osp_population, effective_pipeline_params = parse_population(
        candidates,
        pipeline_params,
        schema,
        forbidden_axes_strict=cycle.config.optimization.forbidden_axes_strict,
    )
    # Resolve the winner's merged pipeline_params by identity, not position: `scored`
    # below is filtered (drops no-result / leader-ineligible candidates), so its index
    # no longer aligns with this full-population list. Keyed lookup is filter-proof.
    params_by_id = {
        ind.lineage.id: pp
        for ind, pp in zip(osp_population, effective_pipeline_params, strict=True)
    }
    decisions: list[ResumeCheckpointRecord] = []
    (
        all_candidate_results,
        candidate_scores,
        escalation_signal,
        sample_order_timeline,
    ) = await score_population(
        cycle,
        osp_population,
        effective_pipeline_params,
        candidates,
        dataset,
        degradation_checks=degradation_checks,
        callbacks=callbacks,
        pobb_config=pobb_config,
        round_num=round_num,
        decisions=decisions,
        l1_diversity=yield_stats.l1_yield,
        online_reorder=online_reorder,
    )

    aborted_ids = {cs.candidate_id for cs in candidate_scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    # The incumbent floor, scored on the SAME samples the candidates ran. PoBB already
    # backfilled the incumbent (seed) onto every sample a candidate touched, so re-scoring
    # it over the touched union is all cache hits — a real matched origin floor at no added
    # spend, and nothing wasted on subset samples no candidate reached. Probe rounds keep
    # their cumulative re-scope (the incumbent already measured the warned-query set).
    if cycle.probe_next_round:
        origin = cycle.origin_for_round(dataset, round_num)
    else:
        scored_sids = {
            r["sample_id"]
            for results in all_candidate_results.values()
            for r in results
            if r.get("sample_id") is not None
        }
        touched = [s for s in dataset if int(s.id) in scored_sids]
        origin = await rescore_origin(cycle, touched or dataset, round_num, callbacks=callbacks)
    # Full-set origin stats — fallback for `matched_origin` when every candidate ran every sample.
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
    # Elect by confident improvement over MATCHED origin (origin on the candidate's own measured
    # samples), NOT raw accuracy vs origin's full-set rate. The online picker scores each candidate
    # on a different hard-first subset, so origin's full-set accuracy (inflated by the easy samples
    # the candidate never ran) is the wrong comparison floor. Ranking is the paired-fitness LCB vs
    # matched origin (the posterior PoBB eliminates on), tie-broken toward higher coverage; origin is
    # the floor at rank (0.0, 0), so only a candidate confidently above origin (lcb > 0) can win.
    best_rank: tuple[float, int] = (0.0, 0)
    winner_idx: int | None = None
    # An under-probed candidate can't win the round: a subset thinner than the elimination floor is
    # too noisy to trust over a fully-probed incumbent. Clamp to the dataset so a tiny set stays electable.
    coverage_floor = min(pobb_config.n_min, len(dataset))
    # The opt_sp-aware composite + evaluators are computed ONCE in the scoring gateway
    # (`score_search_point` received each candidate's OSP) and ride on its `ScoredCandidate`.
    # Here we only add the matched-origin floor (origin restricted to the candidate's samples)
    # and elect — selection ranks on per-sample fitness LCB, never on composite.
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(candidate_scores)}
    for idx, ind in enumerate(scored):
        cand_results = all_candidate_results[ind.lineage.id]
        cs_idx = cs_by_id.get(ind.lineage.id)
        cs = candidate_scores[cs_idx] if cs_idx is not None else None
        # PoBB-locked candidates may have only run q8/20 — comparing their 8-sample accuracy to
        # origin's full-set rate punishes early-stop. Restrict origin to the candidate's measured set.
        matched = matched_origin_stats(
            cast("list[QueryMeasurement]", origin.results),
            cand_results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        if cs is not None and cs_idx is not None:
            candidate_scores[cs_idx] = cs.model_copy(
                update={
                    "matched_origin_accuracy": matched["accuracy"],
                    "matched_origin_hits": matched["hits"],
                    "matched_origin_composite": matched["composite_fitness"],
                }
            )
        # No origin-floor overlap ⇒ no comparison. A candidate scored on samples the incumbent
        # never ran would "beat" a phantom 0.0 floor (the matched empty-set bug: round 1
        # showed improved=True on 0 hits). The incumbent is re-scored on this round's subset
        # upstream (`rescore_origin`), so this is the safety net for any partial-coverage gap.
        if matched["total"] == 0 or cs is None:
            continue
        cand_base = _compute_accuracy(cand_results)
        if cand_base["total"] < coverage_floor:
            continue
        # Running max of the paired-fitness LCB vs matched origin, tie-broken toward higher
        # coverage. A candidate wins the round only when it confidently clears origin on its own
        # measured samples — not on a lucky subset mean.
        _, lcb = _paired_delta_lcb(
            cand_results, list(cast("list[QueryMeasurement]", origin.results))
        )
        rank = (lcb, cand_base["total"])
        if rank > best_rank:
            best_rank = rank
            best_acc = cs.accuracy
            best_comp = cs.composite_fitness
            best_osp = ind
            best_results = list(cand_results)
            best_label = ind.lineage.changes_description or ind.lineage.id[:12]
            best_scores = dict(cs.evaluators or {})
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
    if base["total"] > 0 and winner_idx is not None:
        # Significance on the per-sample reciprocal-rank FITNESS (the chosen decision metric),
        # not binary hits: a candidate that lifts ground-truth's rank without yet landing it at
        # rank 1 is real improvement on the smooth signal, and a binary-hit test is blind to it.
        # One-sided paired-difference posterior — the same machinery PoBB elimination runs.
        cand_fit, origin_fit = _paired_fitness(
            best_results, list(cast("list[QueryMeasurement]", origin.results))
        )
        if cand_fit:
            mean_d, se_d, _ = paired_diff_posterior(cand_fit, origin_fit)
            if se_d > 1e-12:
                from scipy.stats import norm

                p_value = float(norm.sf(mean_d / se_d))
            else:
                p_value = 0.0 if mean_d > 0 else 1.0

    delta_ok = best_acc > best_matched_origin_acc + improvement_threshold
    n_min = pobb_config.n_min
    n_ok = base["total"] >= n_min
    sig_ok = not round_significance_gate or (
        p_value is not None and p_value < improvement_significance
    )
    # Anchor the promotion verdict to the FROZEN round-0 origin (C0), not just the current
    # incumbent's matched floor. The incumbent re-anchors to whatever won last round, so each
    # promotion lowers the bar; requiring the winner to also clear C0 stops the lineage from
    # decaying below where it started while still stamping `improved=True`.
    c0_floor = cycle.tracking.origin_accuracy
    c0_ok = best_acc >= c0_floor
    improved = delta_ok and n_ok and sig_ok and c0_ok
    improved_reason: str | None = None
    if delta_ok and not improved:
        reasons: list[str] = []
        if not n_ok:
            reasons.append(f"n={base['total']} < elimination_n_min={n_min}")
        if not sig_ok:
            p_repr = f"{p_value:.3f}" if p_value is not None else "None"
            reasons.append(f"p={p_repr} >= {improvement_significance:.2f}")
        if not c0_ok:
            reasons.append(f"acc={best_acc:.3f} < origin_c0={c0_floor:.3f}")
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
        pipeline_params=params_by_id.get(winner_id, pipeline_params)
        if winner_id
        else pipeline_params,
        results=best_results,
        all_candidate_results=dict(all_candidate_results),
        sample_order_timeline=sample_order_timeline,
        candidates_scored=len(scored),
        candidate_scores=candidate_scores,
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
