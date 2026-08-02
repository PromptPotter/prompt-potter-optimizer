"""Round-winner selection. `l1_score` scores via `score_population`, adds the matched-origin
floor, elects by `round_winner_key`, produces `RoundResult`. Composite + evaluators are
opt_sp-aware from the scoring gateway (no recompute here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.exploration import theta_accuracy_ci
from promptpotter.application.optimization.l1.population import parse_population
from promptpotter.application.optimization.l1.score.loop import (
    replicate_survivors_pass,
    score_population,
)
from promptpotter.application.optimization.pobb.checks import PoBBConfig
from promptpotter.application.optimization.resume_and_fork.decisions import (
    ResumeCheckpointKind,
    record_decision,
)
from promptpotter.application.optimization.validators.l1_strict import L1YieldStats
from promptpotter.application.origin import rescore_parent
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
    is_leader_eligible,
)
from promptpotter.domain.scoring import QueryMeasurement, is_answer_collapsed
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

    aborted_ids = {cs.candidate_id for cs in candidate_scores if not is_leader_eligible(cs)}
    scored = [
        ind
        for ind in opt_sp_population
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
    # Opt-in replication of the PARENT reference — the shared comparison anchor for the
    # θ election + paired diff, so its single-draw noise floods every candidate's comparison
    # (correlated across arms — the 0.808 the variance read found). Give it `rep_k` extra
    # force_fresh draws too, but thread them ONLY into the decision estimators
    # (`parent_election_results`); the base single draw stays the matched DISPLAY floor so
    # its cell count stays honest.
    parent_election_results: list[QueryMeasurement] = list(
        cast("list[QueryMeasurement]", parent.results)
    )
    if rep_k > 0:
        for _ in range(rep_k):
            extra = await rescore_parent(
                cycle, dataset, round_num, callbacks=callbacks, force_fresh=True
            )
            parent_election_results.extend(cast("list[QueryMeasurement]", extra.results))
    # Full-set parent stats — fallback for `matched_origin` when every candidate ran every sample.
    # No-winner headline = the RETAINED incumbent re-scored on THIS round's touched subset
    # (`rescore_parent`, above) — one configuration, on named samples, actually measured.
    #
    # It used to be `tracking.current_*`, the cumulative frontier, on the argument that the
    # subset re-score deflates a held round (the touched subset is the incumbent's own hard
    # failures, so ~20% can stand for a prompt whose full-set rate is ~42%). That is a real
    # effect, but the cure was worse: the frontier is a sample-keyed union of rows measured by
    # DIFFERENT configurations, so a held round published a number no individual ever scored.
    # Measured on `justlogic-d234__f3af53` round 6 — candidates at 0.0 and 0.481, nobody
    # crowned — the record read `accuracy 0.75` over 40 samples and became the cycle's best
    # round. A deflated real measurement beats a flattering fabricated one, and the round's own
    # `total` carries the denominator that explains it.
    #
    # It also makes the series ONE quantity: a winner round already reports the winner on this
    # round's subset, so every round now reads "whoever holds the lineage, measured on the
    # samples this round drew" instead of alternating between two incomparable things.
    # `results`/`total` source from the SAME re-score so they stay mutually consistent
    # (the health failure rates + the
    # evidence-starved→L2 rate denominate over `results` — the attempted rows — internally).
    # All nine are overwritten when a winner is elected, so this only shapes the no-winner record.
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
        candidate_scores[cs_idx] = candidate_scores[cs_idx].model_copy(
            update={
                "matched_origin_accuracy": matched["accuracy"] if matched else None,
                "matched_origin_composite": matched["composite_fitness"] if matched else None,
            }
        )
        # A candidate that answered ONE label to every sample is not a weak candidate — it carries
        # no measurement of ability at all, and its accuracy is decided by how much of that label
        # the drawn subset happens to contain. Leaving it electable fits θ to that artifact and
        # feeds it to the L4 trajectory, where one such arm dragged its round 0.8 logits below
        # origin. It keeps its matched-origin stamp above (the record stays honest); it just stops
        # being a thing the election, the ruler, or the outer proxy can read. The round-level fact
        # — that this optimizer prompt made its children stop answering — is charged separately, to
        # the L4 floor + PoBB elimination (`domain/l4/proxies.py::floor_reason`).
        if is_answer_collapsed(cand_results):
            continue
        electable.append(ind.lineage.id)

    # Replicated candidate: its pass-1 composite/accuracy/CI predate the extra draws, but the θ
    # election read every row — recompute the displayed point, and the whisker around it, over
    # ALL rows so they match the decision. Guarded on genuine replication (rows > distinct
    # cells); at the ``rep_k=0`` default nothing here fires and every candidate keeps the CI
    # its OWN report stamped when it finished scoring (``build_score_report``), which is what
    # lets a mid-round bar carry a whisker at all.
    if rep_k > 0:
        opt_sp_by_id = {ind.lineage.id: ind for ind in opt_sp_population}
        for cs_idx, cs in enumerate(candidate_scores):
            rows = all_candidate_results.get(cs.candidate_id)
            opt_sp = opt_sp_by_id.get(cs.candidate_id)
            if not rows or opt_sp is None:
                continue
            n_cells = len({r.get("sample_id") for r in rows if r.get("sample_id") is not None})
            if len(rows) <= n_cells:
                continue
            s = compute_composite_fitness(
                rows,
                schema,
                opt_sp=opt_sp,
                round_scorer=session.scoring.round_scorer,
                l1_diversity=yield_stats.l1_yield,
            )
            ci_lo, ci_hi = composite_ci(rows)
            candidate_scores[cs_idx] = cs.model_copy(
                update={
                    "composite_fitness": s["composite_fitness"],
                    "accuracy": s["accuracy"],
                    "total": s["total"],
                    "composite_ci_lo": ci_lo,
                    "composite_ci_hi": ci_hi,
                }
            )

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
    # …and left ``None`` for EVERY candidate when the ruler is still cold, which is the whole
    # of the guard below. On a flat ruler ``fit_theta_given_delta`` places every sample at δ=0
    # and θ degenerates to logit-accuracy over the candidate's OWN subset — monotone in the
    # number sitting next to it, so it explains nothing the accuracy does not already say, and
    # it is not on the shared scale the name θ promises. A fresh campaign is cold for its first
    # round or two (``Cycle.start`` sees only C0, which cannot identify δ), the ruler then warms
    # inside ``absorb_round`` — AFTER this stamp — and ``_maybe_warm_ruler`` re-fits the round's
    # ``cumulative_theta`` and re-stamps its ``calibration_model``, but not these rows. So the
    # round file used to carry a warm round-θ and a "1PL" stamp over cold candidate-θ, and the
    # lineage tree rendered those cold values under the same θ toggle as every later round's
    # warm ones. Absent is the honest answer; ``accuracy`` still carries what was measured.
    #
    # Guarded here rather than by emptying ``electable``: that list is also the resume
    # decision's ``candidate_ids`` and the round's ``electable_count``, and the ELECTION is
    # still valid on a cold ruler (it ranks the arms it has). Only the stamp is suppressed.
    for cid in electable if cycle.delta_scale else ():
        theta_c = abilities.theta.get(cid)
        if theta_c is None:
            continue
        cs_idx = cs_by_id[cid]
        cs = candidate_scores[cs_idx]
        theta_se_c = abilities.theta_se[cid]
        theta_update: dict[str, float] = {"theta": theta_c, "theta_se": theta_se_c}
        # Show the difficulty-adjusted ability band (what the election ranks on) as the whisker
        # only where it brackets the bar's quantity: warm ruler AND composite == accuracy.
        if abs(cs.composite_fitness - cs.accuracy) < 1e-9:
            band = theta_accuracy_ci(theta_c, theta_se_c, cycle.delta_scale)
            if band is not None:
                theta_update["composite_ci_lo"], theta_update["composite_ci_hi"] = band
        candidate_scores[cs_idx] = cs.model_copy(update=theta_update)
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

    base = _compute_accuracy(best_results)
    # The headline sample count rides the winner's ScoredCandidate row — the replication
    # block above updates it over ALL rows, so the round header and the
    # per-candidate table cannot disagree under ``replicate_survivors > 0``.
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
    # There is deliberately NO second sample-count gate here. ``coverage_floor`` (above) is the
    # ONE under-probing guard, and ``delta_ok`` requires a winner — so by this line the election
    # has already refused to crown anything below it. A re-check against the UNCLAMPED
    # ``elimination_n_min`` only ever differed where the floor was clamped (``min(n_min,
    # len(dataset))``, so "a tiny set stays electable"), and there it contradicted that clamp
    # outright: on a dataset smaller than ``elimination_n_min`` the election crowned a winner and
    # this gate then refused to call the round improved — forever, since the samples do not
    # exist. One number cannot be both a stopping floor and an election floor with two different
    # clampings; the election owns electability.
    #
    # ``delta_ok`` is the WHOLE verdict. A second floor used to sit beside it, ``c0_ok``,
    # requiring the winner to also clear the FROZEN round-0 origin's theta — justified in a
    # comment as "stops the lineage from decaying below where it started". It never did that:
    # this verdict feeds the stall counter and the life bank and nothing else, while adoption
    # happens unconditionally in ``absorb_round``. A winner below C0 was crowned, adopted and
    # mutated from regardless of what this line said, so the protection was described but not
    # implemented.
    #
    # What it did do was compare across evidence bases. The winner's theta was fit on THIS
    # round's panel; ``origin_round.cumulative_theta`` was fit on round 0's; and both sat on a
    # ruler calibrated from C0 alone, where C0 is 0.000 by the identifiability anchor. So the
    # floor asked "does this candidate agree with round 0's hit pattern", and it was strictly
    # harder to clear than the same-panel comparison ``delta_ok`` already runs. Measured on
    # `justlogic-d234`, 2026-07-27: two rounds carrying +14.3pp over their matched parent at
    # p=0.017 and p=0.046 were stamped `improved=False` here, each costing a life, and both
    # campaigns then died on the empty bank while still holding a real lift over C0. The ruler
    # is now identified before it is trusted (``cycle.py::_calibrate_delta_ruler``), which fixes
    # the scale — but two floors on one verdict, one of them cross-panel, is one mechanism too
    # many. The lineage's standing against C0 stays RECORDED, not gated: ``origin_accuracy`` and
    # the ``matched_origin_*`` triple ride every round.
    improved = delta_ok
    # Name the estimator that actually decided, and separate the two ways a round holds. One
    # string used to cover both — "no winner cleared the matched parent by {t} accuracy" — and
    # it was wrong twice over: the comparison has run in θ-logits since the election moved to
    # the fixed ruler, and it read as "somebody was crowned and fell short" on rounds where the
    # election crowned nobody. Fifteen of the banked rounds carry that sentence, which is where
    # the belief that this gate reads accuracy came from; the L4 narrative and the outer L1
    # critique both quote it, so the outer loop was being taught the wrong lever.
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
    )
    return round_result, best_opt_sp


__all__ = ["l1_score"]
