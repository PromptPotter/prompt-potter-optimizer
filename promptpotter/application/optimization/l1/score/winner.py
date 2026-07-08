"""Round-winner selection. `l1_score` scores via `score_population`, adds the matched-origin
floor, elects by `round_winner_key`, produces `RoundResult`. Composite + evaluators are
opt_sp-aware from the scoring gateway (no recompute here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.l1.population import parse_population
from promptpotter.application.optimization.l1.score.loop import (
    replicate_survivors_pass,
    score_population,
)
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
    composite_ci,
    compute_composite_fitness,
    count_degraded_samples,
    elect_round_winner,
    matched_origin_stats,
    paired_fitness,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
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

# Floor on the logistic slope p(1−p) when recalibrating the accuracy-space
# ``improvement_threshold`` into θ-logits (``delta_ok``). Caps how large the logit
# threshold grows near the accuracy extremes (p→0 or 1), where p(1−p)→0 would blow it up.
_GATE_SLOPE_FLOOR = 0.05


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


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list[Sample],
    *,
    pipeline_params: dict[str, Any] | None = None,
    improvement_threshold: float = 0.01,
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
    )

    aborted_ids = {cs.candidate_id for cs in candidate_scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in osp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    # Opt-in successive-halving replication: give survivors extra independent draws BEFORE the
    # estimators run, so the per-cell mean (and the θ fit) averages out an idiosyncratic
    # single-run inner draw. Losers were already PoBB-eliminated (not in `scored`), so only
    # survivors pay the k× spend. Off by default (`replicate_survivors == 0`).
    rep_k = cycle.config.optimization.replicate_survivors
    if rep_k > 0 and scored:
        await replicate_survivors_pass(
            cycle,
            scored,
            {ind.lineage.id: params_by_id[ind.lineage.id] for ind in scored},
            all_candidate_results,
            dataset,
            rep_k,
        )
    # The incumbent floor, scored on the SAME samples the candidates ran. PoBB already
    # backfilled the incumbent (seed) onto every sample a candidate touched, so re-scoring
    # it over the touched union is all cache hits — a real matched origin floor at no added
    # spend, and nothing wasted on subset samples no candidate reached. Probe rounds keep
    # their cumulative re-scope (the incumbent already measured the warned-query set).
    if cycle.probe_next_round:
        origin_scoring_set = dataset
        origin = cycle.origin_for_round(dataset, round_num)
    else:
        scored_sids = {
            r["sample_id"]
            for results in all_candidate_results.values()
            for r in results
            if r.get("sample_id") is not None
        }
        touched = [s for s in dataset if int(s.id) in scored_sids]
        origin_scoring_set = touched or dataset
        origin = await rescore_origin(cycle, origin_scoring_set, round_num, callbacks=callbacks)
    # Opt-in replication of the ORIGIN reference. Origin is the shared comparison anchor for the
    # θ election + paired diff, so its single-draw noise floods every candidate's comparison
    # (correlated across arms — the 0.808 the variance read found). Give it `rep_k` extra
    # force_fresh draws too, but thread them ONLY into the decision estimators
    # (`origin_election_results`); the base single draw stays the matched-origin DISPLAY floor so
    # its cell count stays honest. Probe rounds keep their own re-scope (not replicated).
    origin_election_results: list[QueryMeasurement] = list(
        cast("list[QueryMeasurement]", origin.results)
    )
    if rep_k > 0 and not cycle.probe_next_round:
        for _ in range(rep_k):
            extra = await rescore_origin(
                cycle, origin_scoring_set, round_num, callbacks=callbacks, force_fresh=True
            )
            origin_election_results.extend(cast("list[QueryMeasurement]", extra.results))
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
    # the candidate never ran) is the wrong comparison floor. ``elect_round_winner`` ranks the
    # paired-fitness LCB vs matched origin, tie-broken toward higher coverage — the ONE election
    # rule, shared with the resume divergence replayer so a resumed run re-elects the same winner.
    #
    # An under-probed candidate can't win: a subset thinner than the elimination floor is too noisy
    # to trust over a fully-probed incumbent. Clamp to the dataset so a tiny set stays electable.
    coverage_floor = min(pobb_config.n_min, len(dataset))
    # The opt_sp-aware composite + evaluators are computed ONCE in the scoring gateway
    # (`score_search_point` received each candidate's OSP) and ride on its `ScoredCandidate`.
    # Here we only add the matched-origin floor (origin restricted to the candidate's samples).
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(candidate_scores)}
    matched_by_id: dict[str, dict[str, Any]] = {}
    electable: list[str] = []
    for ind in scored:
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is None:
            continue
        cand_results = all_candidate_results[ind.lineage.id]
        # PoBB-locked candidates may have only run q8/20 — comparing their 8-sample accuracy to
        # origin's full-set rate punishes early-stop. Restrict origin to the candidate's measured set.
        matched = matched_origin_stats(
            cast("list[QueryMeasurement]", origin.results),
            cand_results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        matched_by_id[ind.lineage.id] = matched
        candidate_scores[cs_idx] = candidate_scores[cs_idx].model_copy(
            update={
                "matched_origin_accuracy": matched["accuracy"],
                "matched_origin_hits": matched["hits"],
                "matched_origin_composite": matched["composite_fitness"],
            }
        )
        electable.append(ind.lineage.id)

    # Composite-fitness CI — always stamped for any candidate with ≥1 scored sample (broader
    # than ``electable``: eliminated/under-coverage candidates still get one). No composite
    # point estimate should stand alone in the round record or the dashboard.
    osp_by_id = {ind.lineage.id: ind for ind in osp_population}
    for cs_idx, cs in enumerate(candidate_scores):
        rows = all_candidate_results.get(cs.candidate_id)
        if not rows:
            continue
        ci_lo, ci_hi = composite_ci(rows)
        if ci_lo is None:
            continue
        update: dict[str, Any] = {"composite_ci_lo": ci_lo, "composite_ci_hi": ci_hi}
        # Replicated candidate: its pass-1 composite/accuracy predate the extra draws, but the θ
        # election read every row — recompute the displayed point over ALL rows so it matches the
        # decision. Guarded on genuine replication (rows > distinct cells), so the n=1 default is
        # byte-identical; same opt_sp + l1_diversity as the gateway's pass-1 compute.
        n_cells = len({r.get("sample_id") for r in rows if r.get("sample_id") is not None})
        osp = osp_by_id.get(cs.candidate_id)
        if rep_k > 0 and len(rows) > n_cells and osp is not None:
            s = compute_composite_fitness(
                rows,
                schema,
                opt_sp=osp,
                round_scorer=session.scoring.round_scorer,
                l1_diversity=yield_stats.l1_yield,
            )
            update["composite_fitness"] = s["composite_fitness"]
            update["accuracy"] = s["accuracy"]
        candidate_scores[cs_idx] = cs.model_copy(update=update)

    # ``coverage_floor`` is persisted so the replayer applies the same electability floor — without
    # it a resumed run could elect a thin candidate the live path rejected, manufacturing divergence.
    winner_id, abilities = elect_round_winner(
        electable,
        all_candidate_results,
        origin_election_results,
        coverage_floor,
        cycle.delta_scale or {},
    )
    # Stamp each electable candidate's difficulty-adjusted ability θ from the SAME fixed-ruler
    # fit the election just ran (no second fit) — the subset-invariant metric the winner was
    # elected on, so the dashboard can show *why* a lower-accuracy candidate won. Left ``None``
    # for candidates outside the election fit (eliminated / under the coverage floor).
    for cid in electable:
        theta_c = abilities.theta.get(cid)
        if theta_c is None:
            continue
        cs_idx = cs_by_id[cid]
        candidate_scores[cs_idx] = candidate_scores[cs_idx].model_copy(
            update={"theta": theta_c, "theta_se": abilities.theta_se.get(cid, 0.0)}
        )
    record_decision(
        decisions,
        ResumeCheckpointKind.ROUND_WINNER,
        {
            "candidate_ids": electable,
            "round_num": round_num,
            "coverage_floor": coverage_floor,
        },
        winner_id,
        data={"current_best_accuracy_at_record": origin.accuracy},
        round=round_num,
    )
    if winner_id:
        winner_ind = next(ind for ind in scored if ind.lineage.id == winner_id)
        winner_cs = candidate_scores[cs_by_id[winner_id]]
        matched = matched_by_id[winner_id]
        best_acc = winner_cs.accuracy
        best_comp = winner_cs.composite_fitness
        best_osp = winner_ind
        best_results = list(all_candidate_results[winner_id])
        best_label = winner_ind.lineage.changes_description or winner_ind.lineage.id[:12]
        best_scores = dict(winner_cs.evaluators or {})
        best_matched_origin_acc = matched["accuracy"]
        best_matched_origin_hits = matched["hits"]
        best_matched_origin_composite = matched["composite_fitness"]

    base = _compute_accuracy(best_results)
    p_value: float | None = None
    if base["total"] > 0 and winner_id:
        # Recorded diagnostic (shown in the round view); does not gate promotion.
        # Significance on the per-sample reciprocal-rank FITNESS (the chosen decision metric),
        # not binary hits: a candidate that lifts ground-truth's rank without yet landing it at
        # rank 1 is real improvement on the smooth signal, and a binary-hit test is blind to it.
        # One-sided paired-difference posterior — the same machinery PoBB elimination runs.
        cand_fit, origin_fit = paired_fitness(best_results, origin_election_results)
        if cand_fit:
            mean_d, se_d, _ = paired_diff_posterior(cand_fit, origin_fit)
            if se_d > 1e-12:
                from scipy.stats import norm

                p_value = float(norm.sf(mean_d / se_d))
            else:
                p_value = 0.0 if mean_d > 0 else 1.0

    # ``delta_ok`` gates on the winner's difficulty-adjusted ability lift over the matched
    # origin — winner + origin (folded in as ``ORIGIN_ABILITY_ID``) on the cycle's FIXED δ ruler,
    # the same subset-invariant θ scale the election ranks by, not raw subset accuracy. The
    # accuracy-space ``improvement_threshold`` is recalibrated to θ-logits per round by local
    # linearization at the matched-origin operating point (σ' = p(1−p)), so the knob keeps
    # meaning "min accuracy delta" while the comparison happens in θ. Under the default
    # (``per_round_resubset`` OFF) winner and origin share samples, so this tracks the old
    # accuracy gate to first order; it diverges — correctly — only once subsets drift per
    # candidate, the exact case where subset accuracy is no longer comparable.
    delta_ok = False
    if winner_id:
        from promptpotter.application.intelligence.exploration import ORIGIN_ABILITY_ID

        # θ decouples per candidate given the FIXED δ ruler (``fit_theta_given_delta``), so the
        # election's ``abilities`` posterior already carries the winner's and origin's θ on that
        # ruler — the exact fit both gates need. Read it, don't refit (was a deterministic re-run
        # of the same 1-D MAP over the same rows).
        theta_lift = abilities.theta.get(winner_id, 0.0) - abilities.theta.get(
            ORIGIN_ABILITY_ID, 0.0
        )
        slope = max(best_matched_origin_acc * (1.0 - best_matched_origin_acc), _GATE_SLOPE_FLOOR)
        delta_ok = theta_lift > improvement_threshold / slope
    n_min = pobb_config.n_min
    n_ok = base["total"] >= n_min
    # Anchor the promotion verdict to the FROZEN round-0 origin (C0), not just the current
    # incumbent's matched floor. The incumbent re-anchors to whatever won last round, so each
    # promotion lowers the bar; requiring the winner to also clear C0 stops the lineage from
    # decaying below where it started while still stamping `improved=True`.
    #
    # Slice 2 (fitness-comparability): ``c0_ok`` compares in θ on the cycle's FIXED δ ruler —
    # the origin's frozen θ (``cycle.tracking.origin_theta``) vs the winner's θ on that same
    # ruler (``fit_theta_given_delta`` → one shared scale). The ruler is flat (δ≡0) where cold,
    # so θ degenerates to logit-accuracy there: ALWAYS θ, never a separate accuracy branch
    # (one ruler, θ always). Holds up once per-round subsets drift, where raw accuracy stops
    # being cross-round comparable.
    origin_theta = cycle.tracking.origin_theta
    # Same decoupling: the winner's θ on the fixed ruler is the election's ``abilities`` value —
    # the c0_ok floor compares it against the FROZEN round-0 ``origin_theta`` (a different,
    # cross-round number kept as-is), so only the winner-θ side is the deduplicated refit.
    winner_theta = abilities.theta.get(winner_id) if winner_id else None
    if winner_theta is not None and origin_theta is not None:
        c0_ok = winner_theta >= origin_theta
        c0_desc = f"θ={winner_theta:.3f} < origin_c0_θ={origin_theta:.3f}"
    else:
        # No winner / no measured obs / no origin θ (degenerate empty origin) — nothing to
        # floor against, so c0_ok can't block (delta_ok already gates a real winner).
        c0_ok = True
        c0_desc = ""
    improved = delta_ok and n_ok and c0_ok
    improved_reason: str | None = None
    if delta_ok and not improved:
        reasons: list[str] = []
        if not n_ok:
            reasons.append(f"n={base['total']} < elimination_n_min={n_min}")
        if not c0_ok:
            reasons.append(c0_desc)
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
        winner_task_context=(
            best_osp.memory.task_context.to_dict() if best_osp.memory.task_context else None
        ),
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


__all__ = ["is_leader_eligible", "l1_score"]
