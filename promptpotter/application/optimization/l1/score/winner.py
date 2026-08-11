"""Round-winner selection. Composite and evaluators are searchpoint-aware from the scoring gateway — nothing is
recomputed here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimization.dispatch.llm_call.prompts import (
    compute_optimizer_prompt_hashes,
)
from promptpotter.application.optimization.l1.population import parse_population
from promptpotter.application.optimization.l1.score.loop import (
    score_population,
)
from promptpotter.application.optimization.pobb.checks import PoBBConfig
from promptpotter.application.optimization.resume_and_fork.decisions import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.validators.l1_invariants import L1YieldStats
from promptpotter.application.origin import rescore_parent
from promptpotter.application.scoring.diagnostics import count_degraded_samples
from promptpotter.application.scoring.metrics import _compute_accuracy, matched_origin_stats
from promptpotter.application.scoring.selection import (
    elect_round_winner,
    matched_origin_lift,
    paired_fitness,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.results import (
    CandidateProposal,
    RoundResult,
    ScoredCandidate,
    is_electable,
    is_leader_eligible,
)
from promptpotter.domain.scoring import QueryMeasurement
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared.statistics import paired_diff_posterior

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample

# Floor on the logistic slope p(1−p) when recalibrating the accuracy-space
# ``improvement_threshold`` into θ-logits (``delta_ok``). Caps how large the logit
# threshold grows near the accuracy extremes (p→0 or 1), where p(1−p)→0 would blow it up.
_GATE_SLOPE_FLOOR = 0.05


def _warn_if_not_separable(round_num: int, electable: list[ScoredCandidate]) -> None:
    """The round measured cleanly and resolved nothing — the one degradation that is SILENT on
    every other channel, because a winner was still crowned and every number still reads."""
    # Deliberately not a decision. The winner is the best of what the round saw and stands; what
    # this says is that the reader may not treat the margin as a result. Suppressed when no arm
    # carries an interval at all: below two shared cells there is nothing to be inconclusive
    # ABOUT, and a warning on every round of a one-cell panel is noise, not a finding.
    bracketed = [c for c in electable if c.matched_origin_lift_ci_lo is not None]
    if not bracketed or any(
        (c.matched_origin_lift_ci_lo or 0.0) > 0.0 or (c.matched_origin_lift_ci_hi or 0.0) < 0.0
        for c in bracketed
    ):
        return
    widest = max(bracketed, key=lambda c: c.matched_origin_lift_ci_hi or 0.0)
    emit_round_warning(
        kind="round_not_separable",
        message=(
            f"round {round_num} resolved nothing: every one of its {len(bracketed)} readable arms "
            f"has a lift interval spanning 0 (best reaches {widest.matched_origin_lift_ci_hi:+.3f} "
            "at its upper bound). A winner was still elected — read it as the best of what this "
            "round saw, not as a measured improvement over the origin"
        ),
        detail={"arms": len(bracketed), "best_ci_hi": widest.matched_origin_lift_ci_hi},
    )


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list[Sample],
    *,
    pipeline_params: dict[str, Any] | None = None,
    improvement_threshold: float,
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    pobb_config: PoBBConfig,
    round_num: int = 0,
    yield_stats: L1YieldStats,
) -> tuple[RoundResult, OptSearchPoint]:
    """Score + select winner. Returns `(RoundResult, winner_opt_sp)` — RoundResult is persistence
    shape, winner_opt_sp rides alongside for the caller's `PromptVersion` tracing emit.
    """
    session = cycle.session
    schema = session.pipeline_schema
    assert schema is not None, "l1_score requires pipeline_schema"

    opt_sp_population, effective_pipeline_params = parse_population(
        candidates,
        pipeline_params,
        schema,
        prompt_block_catalogue=cycle.config.optimization.prompt_block_catalogue,
    )
    # Resolve the winner's merged pipeline_params by identity, not position: `scored`
    # below is filtered (drops no-result / leader-ineligible candidates), so its index
    # no longer aligns with this full-population list. Keyed lookup is filter-proof.
    params_by_id = {
        ind.lineage.id: pp
        for ind, pp in zip(opt_sp_population, effective_pipeline_params, strict=True)
    }
    # THE cycle's decision sink — the same list every escalation decision uses, which
    # `persist_round` flushes to the ledger and folds into the round file. A local list here
    # meant the election reached the round file only: the ledger, the declared sole ingress,
    # never learned who won, so a served view had to read another projection's output to
    # find out.
    decisions = cycle.pending_decisions
    (
        all_candidate_results,
        candidate_scores,
        escalation_signal,
    ) = await score_population(
        cycle,
        opt_sp_population,
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

    # The REPLICATION cohort, deliberately the looser predicate: `scored` decides who gets extra
    # draws, and collapse is a verdict on rows we would be about to add to. Admission to the
    # ELECTION is `is_electable` below, over the rows this pass ends up with.
    aborted_ids = {cs.candidate_id for cs in candidate_scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in opt_sp_population
        if ind.lineage.id in all_candidate_results and ind.lineage.id not in aborted_ids
    ]
    # The parent floor, scored on the round's WHOLE panel — the samples this round drew, not
    # the union the candidates happened to reach before elimination truncated them.
    #
    # The matched floor does not need the narrower set: ``matched_origin_stats`` builds each
    # candidate's comparison by RESTRICTING these rows to that candidate's own measured
    # samples, so a wider parent leaves every election comparison byte-identical. What the
    # narrow set changed was the round's published headline, which on a held round IS this
    # re-score — and it inherited the collapse of whatever PoBB cut. Measured on the
    # 2026-07-27 inner campaigns: two of four terminated reporting ``accuracy 0.250`` over
    # **n=8**, because every candidate was cut at 8 samples and the unchanged champion that had
    # read 0.571 the round before was re-scored over that 8-sample union. Both then stopped on
    # an exhausted life bank, so the number the operator was handed as the collapse was in fact
    # a denominator. A round's headline denominates over the round's panel or it is not a
    # series. The added spend is the truncated tail only, and a retained champion replays from
    # cache whatever it has already measured.
    parent = await rescore_parent(cycle, dataset, round_num, callbacks=callbacks)
    # The round's scoring ends here, so an armed look-ahead is spent here — inside the scoring
    # phase, not at the round boundary, so a press landing during critique waits for the next round
    # instead of being consumed having sped up nothing. Not in a `finally`: an unwound round did
    # not score, and `clear_run_control_flags` drops the residue at the next launch.
    if session.sample_lookahead_consume is not None:
        session.sample_lookahead_consume()
    # The PARENT reference — the shared comparison anchor for the θ election + paired diff.
    # Its single-draw noise is correlated across arms, so it floods every candidate's
    # comparison equally rather than favouring one (the 0.808 the variance read found).
    parent_election_results: list[QueryMeasurement] = list(
        cast("list[QueryMeasurement]", parent.results)
    )
    # Full-set parent stats — fallback for `matched_origin` when every candidate ran every sample.
    # No-winner headline = the RETAINED incumbent re-scored on THIS round's touched subset
    # (`rescore_parent`, above) — one configuration, on named samples, actually measured.
    #
    # Never publish `tracking.current_*` here: the frontier unions rows measured by DIFFERENT
    # configurations, so a held round reports a number no individual scored. The subset
    # re-score does deflate, and a deflated real measurement beats a fabricated one — `total`
    # carries the denominator. `results`/`total` source from the SAME re-score. All nine are
    # overwritten when a winner is elected, so this only shapes the no-winner record.
    best_acc = parent.report.accuracy
    best_comp = parent.report.composite_fitness
    # The parent reference shown beside the headline must share its sample basis, or a held
    # round renders "accuracy 58% (origin 18%)" — a phantom lift. No winner ⇒ both are the
    # incumbent's standing (lift 0); a winner keeps the touched matched reference (set below).
    best_origin_accuracy = parent.report.accuracy
    best_opt_sp: OptSearchPoint = parent.opt_sp
    best_results: list[QueryMeasurement] = list(cast("list[QueryMeasurement]", parent.results))
    best_label = parent.report.label
    best_scores: dict[str, float] = dict(parent.report.evaluators)
    best_matched_origin_acc: float | None = parent.report.accuracy
    best_matched_origin_composite: float | None = parent.report.composite_fitness
    # Not seeded from the parent like its two neighbours: the parent's lift over ITSELF is 0 by
    # construction, and a round that crowned nobody publishing "+0.000" reads as a measured tie.
    best_lift: tuple[float | None, float | None, float | None] = (None, None, None)
    # Elect by confident improvement over MATCHED origin (origin on the candidate's own measured
    # samples), NOT raw accuracy vs origin's full-set rate. Candidates share ONE round order but
    # elimination truncates them at different depths, so each ends up measured on a different
    # prefix of it — origin's full-set accuracy (inflated by the samples the candidate never
    # reached) is the wrong comparison floor. ``elect_round_winner`` ranks the
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
    matched_by_id: dict[str, dict[str, Any] | None] = {}
    electable: list[str] = []
    for ind in scored:
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is None:
            continue
        cand_results = all_candidate_results[ind.lineage.id]
        # ``None`` unless this candidate covered the origin's whole panel. The round order is
        # stratified on the incumbent's own grades, so the origin's rate on a truncated
        # candidate's prefix is decided by where PoBB stopped it, not by the data — see
        # ``matched_origin_stats``. A cut candidate's standing is its θ, which it keeps below.
        matched = matched_origin_stats(
            cast("list[QueryMeasurement]", parent.results),
            cand_results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        matched_by_id[ind.lineage.id] = matched
        # The floor and the INTERVAL on the lift over it are stamped together — a point estimate
        # published without its error bar is what let a six-cell panel read as a verdict.
        # Unconditional on ``matched``: that gate asks whether the candidate covered the origin's
        # WHOLE panel, while the lift is defined on the cells both reached, so a PoBB-truncated
        # arm still gets an honest (wider) interval instead of nothing.
        lift = matched_origin_lift(cand_results, cast("list[QueryMeasurement]", parent.results))
        candidate_scores[cs_idx] = candidate_scores[cs_idx].model_copy(
            update={
                "matched_origin_accuracy": matched["accuracy"] if matched else None,
                "matched_origin_composite": matched["composite_fitness"] if matched else None,
                "matched_origin_lift": lift[0] if lift else None,
                "matched_origin_lift_ci_lo": lift[1] if lift else None,
                "matched_origin_lift_ci_hi": lift[2] if lift else None,
            }
        )
        # `is_electable` is the admission rule, spelled once in the domain so the election and the
        # ruler cannot drift apart on it. Its collapse clause is why an
        # arm can be scored here and still not enter: a candidate answering ONE label to every
        # sample carries no measurement of ability, its accuracy is decided by how much of that
        # label the drawn subset happens to contain, and fitting θ to that artifact dragged one
        # round 0.8 logits below origin. It keeps its matched-origin stamp above (the record
        # stays honest). The round-level fact — that this optimizer prompt made its children
        # stop answering — is charged separately, to the L4 floor + PoBB elimination
        # (`domain/l4/proxies.py::floor_reason`).
        if not is_electable(candidate_scores[cs_idx], cand_results):
            continue
        electable.append(ind.lineage.id)

    # This round's candidates are already banked grade-A (``score_search_point`` archives each as
    # it finishes), so the ruler's ≥2-arm floor is satisfiable NOW — and the election below is the
    # thing that needs it. Attempting only after ``absorb_round`` left every election running on a
    # cold ruler, and at L4's two-round budget no later round existed to spend a warm one on.
    # No-op once warm, and still flat when the pool holds one arm.
    cycle.warm_ruler_if_cold()
    # ``coverage_floor`` is persisted so the replayer applies the same electability floor — without
    # it a resumed run could elect a thin candidate the live path rejected, manufacturing divergence.
    winner_id, abilities = elect_round_winner(
        electable,
        all_candidate_results,
        parent_election_results,
        coverage_floor,
        cycle.delta_scale or {},
    )
    # Stamp each electable candidate's difficulty-adjusted ability θ from the SAME fixed-ruler
    # fit the election just ran (no second fit) — the subset-invariant metric the winner was
    # elected on, so the dashboard can show *why* a lower-accuracy candidate won. Left ``None``
    # for candidates outside the election fit (eliminated / under the coverage floor).
    #
    # …and ``None`` for EVERY candidate while the ruler is cold — the guard below. On a flat
    # ruler θ degenerates to logit-accuracy over the candidate's OWN subset: monotone in the
    # accuracy beside it, and not on the shared scale the name θ promises. Absent is the honest
    # answer. Guarded here rather than by emptying ``electable``, which is also the resume
    # decision's ``candidate_ids``; the election is still valid on a cold ruler.
    for cid in electable if cycle.delta_scale else ():
        theta_c = abilities.theta.get(cid)
        if theta_c is None:
            continue
        cs_idx = cs_by_id[cid]
        cs = candidate_scores[cs_idx]
        # θ and its SE only. The whisker is NOT rewritten here: a second estimator writing the
        # same bounds made the band mean different things on different bars of one chart, and
        # it landed only on arms this loop reaches — electable, warm ruler, fitted — so the
        # whisker appeared and vanished by election gating rather than by evidence. One band,
        # stamped once per candidate at `candidate_scored`, is the whole mechanism now.
        candidate_scores[cs_idx] = cs.model_copy(
            update={"theta": theta_c, "theta_se": abilities.theta_se[cid]}
        )
    _warn_if_not_separable(round_num, [candidate_scores[cs_by_id[cid]] for cid in electable])
    record_decision(
        decisions,
        ResumeCheckpointKind.ROUND_WINNER,
        {
            "candidate_ids": electable,
            "round_num": round_num,
            "coverage_floor": coverage_floor,
        },
        winner_id,
        data={"current_best_accuracy_at_record": parent.report.accuracy},
        round=round_num,
    )
    if winner_id:
        winner_ind = next(ind for ind in scored if ind.lineage.id == winner_id)
        winner_cs = candidate_scores[cs_by_id[winner_id]]
        matched = matched_by_id[winner_id]
        best_acc = winner_cs.accuracy
        best_comp = winner_cs.composite_fitness
        best_origin_accuracy = parent.report.accuracy
        best_opt_sp = winner_ind
        best_results = list(all_candidate_results[winner_id])
        best_label = winner_ind.lineage.changes_description or winner_ind.lineage.id[:12]
        best_scores = dict(winner_cs.evaluators)
        best_matched_origin_acc = matched["accuracy"] if matched else None
        best_matched_origin_composite = matched["composite_fitness"] if matched else None
        best_lift = (
            winner_cs.matched_origin_lift,
            winner_cs.matched_origin_lift_ci_lo,
            winner_cs.matched_origin_lift_ci_hi,
        )

    base = _compute_accuracy(best_results)
    # The headline sample count rides the winner's ScoredCandidate row, so the round header
    # and the per-candidate table cannot disagree.
    best_total = winner_cs.total if winner_id else base["total"]
    p_value: float | None = None
    if base["total"] > 0 and winner_id:
        # Recorded diagnostic (shown in the round view); does not gate promotion.
        # Significance on the per-sample reciprocal-rank FITNESS (the chosen decision metric),
        # not binary hits: a candidate that lifts ground-truth's rank without yet landing it at
        # rank 1 is real improvement on the smooth signal, and a binary-hit test is blind to it.
        # One-sided paired-difference posterior — the same machinery PoBB elimination runs.
        cand_fit, origin_fit = paired_fitness(best_results, parent_election_results)
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
    # meaning "min accuracy delta" while the comparison happens in θ. Winner and origin share
    # this round's panel either way (``rescore_parent`` above re-scores the parent on it), so
    # this tracks an accuracy gate to first order; it diverges — correctly — once elimination
    # truncates candidates to different prefixes of that panel, the exact case where subset
    # accuracy stops being comparable. (``per_round_resubset`` defaults ON —
    # ``config.py::SelectionMechanisms`` — so subsets also drift BETWEEN rounds; the fixed ruler
    # is what keeps θ comparable across them.)
    # Linearize at the ORIGIN's own operating point on this round's panel — a full-panel
    # measurement, and a property of the origin rather than of whichever candidate won. It used
    # to read the winner's ``matched_origin_accuracy``; for a leader-locked winner that is the
    # origin's rate on a prefix of an incumbent-stratified order (``⌊n/4⌋/n``), so the bar the
    # winner had to clear moved with wherever PoBB happened to stop it — up to ~1.8x on a
    # six-sample prefix. No banked round has yet elected a truncated winner, so this closes a
    # latent hole rather than a fired one.
    slope = max(parent.report.accuracy * (1.0 - parent.report.accuracy), _GATE_SLOPE_FLOOR)
    gate_bar = improvement_threshold / slope
    theta_lift: float | None = None
    delta_ok = False
    if winner_id:
        from promptpotter.application.intelligence.exploration import theta_lift_over_origin

        # θ decouples per candidate given the FIXED δ ruler (``fit_theta_given_delta``), so the
        # election's ``abilities`` posterior already carries the winner's and origin's θ on that
        # ruler — the exact fit both gates need. Read it, don't refit (was a deterministic re-run
        # of the same 1-D MAP over the same rows). ``None`` = the origin was never fit (every
        # row errored), so there is no floor to have improved on: the gate stays shut. This is
        # the same policy ``c0_ok`` states below; both read one helper so they cannot drift apart
        # (they had, and this branch was the one inventing a coin-flip origin).
        theta_lift = theta_lift_over_origin(abilities, winner_id)
        delta_ok = theta_lift is not None and theta_lift > gate_bar
    # ``delta_ok`` is the WHOLE verdict — add no second gate beside it. ``coverage_floor``
    # (above) is the ONE under-probing guard and the election owns electability, so a re-check
    # against the unclamped ``elimination_n_min`` only fires where that floor was clamped, and
    # then refuses to call a round improved forever on a dataset smaller than the floor. A
    # ``c0_ok`` floor is the other one not to re-add: it compares across evidence bases (this
    # round's panel vs round 0's) and gates nothing, since adoption is unconditional in
    # ``absorb_round``. The lineage's standing against C0 stays RECORDED, not gated —
    # ``origin_accuracy`` and the ``matched_origin_*`` triple ride every round.
    improved = delta_ok
    # Name the estimator that decided, and keep the two ways a round holds distinct: the
    # comparison runs in θ-logits, and "no winner cleared the matched parent" must not be said
    # of a round that crowned nobody. The L4 narrative and outer L1 critique quote this string,
    # so a wrong one teaches the outer loop the wrong lever.
    if improved:
        improved_reason: str | None = None
    elif not winner_id:
        improved_reason = "no candidate's ability exceeded the parent's on the round's δ ruler"
    elif theta_lift is None:
        improved_reason = "the parent's ability could not be fit, so there was no floor to clear"
    else:
        improved_reason = (
            f"winner's ability lift {theta_lift:+.3f} logits did not clear {gate_bar:.3f} "
            f"({improvement_threshold} accuracy at the parent's {parent.report.accuracy:.0%} rate)"
        )
    round_result = RoundResult(
        round=round_num,
        label=best_label,
        accuracy=best_acc,
        composite_fitness=best_comp,
        total=best_total,
        improved=improved,
        p_value=p_value,
        improved_reason=improved_reason,
        origin_accuracy=best_origin_accuracy,
        matched_origin_accuracy=best_matched_origin_acc,
        matched_origin_composite=best_matched_origin_composite,
        matched_origin_lift=best_lift[0],
        matched_origin_lift_ci_lo=best_lift[1],
        matched_origin_lift_ci_hi=best_lift[2],
        prompt_fields={
            **best_opt_sp.prompt_field_dict(),
            "lineage": best_opt_sp.lineage.model_dump(),
        },
        pipeline_params=params_by_id.get(winner_id, pipeline_params)
        if winner_id
        else pipeline_params,
        results=best_results,
        all_candidate_results=dict(all_candidate_results),
        candidates_scored=len(scored),
        electable_count=len(electable),
        candidate_scores=candidate_scores,
        # `decisions` is NOT set here: `persist_round` flushes the cycle's sink onto this
        # round and onto the ledger in one act, so the round file has one writer.
        degraded_samples=count_degraded_samples(best_results),
        deprecated=base["deprecated"],
        escalation_signal=escalation_signal,
        evaluators=best_scores,
        l1_yield=yield_stats.l1_yield,
        # The collapse COUNTS are not passed: `RoundResult` derives them from
        # `candidate_scores`, which already carries every collapsed candidate with its
        # reason. Passing them here would be a second recording that could disagree.
        l1_parse_failure=yield_stats.l1_parse_failure,
        # WHICH optimizer produced this round — the one fact about a round that a later
        # process cannot re-derive, since every other input to a node's package is live
        # cycle state. Stamped here rather than at save time: a re-save (a repair, a
        # rescore) must not restamp a round with the optimizer running NOW and erase the
        # evidence that it ran under a different one.
        optimizer_prompt_hashes=compute_optimizer_prompt_hashes(),
    )
    return round_result, best_opt_sp


__all__ = ["l1_score"]
