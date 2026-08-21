"""Round-winner selection. Composite and evaluators are searchpoint-aware from the scoring gateway — nothing is
recomputed here."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.intelligence.exploration import PARENT_ABILITY_ID
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
from promptpotter.application.scoring.metrics import _compute_accuracy, matched_parent_stats
from promptpotter.application.scoring.selection import (
    distinct_valid_cells,
    elect_round_winner,
    matched_parent_lift,
    paired_fitness,
    parent_cells,
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
from promptpotter.domain.search_point import strip_rendered_prompt
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.llm.telemetry import emit_round_warning
from promptpotter.shared.statistics import paired_reading

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.application.intelligence.exploration import RaschPosterior
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample


def _theta(value: float | None, *, signed: bool = True) -> str:
    """A θ that was never fit prints as ``n/a`` — never as ``0.000``, which is a real ability.
    ``signed=False`` for an SE, which has no direction to carry a leading ``+``."""
    if value is None:
        return "n/a"
    return f"{value:+.3f}" if signed else f"{value:.3f}"


def _verdict_reason(
    *,
    winner_id: str,
    electable: Sequence[str],
    abilities: RaschPosterior,
    labels: Mapping[str, str],
    coverage_floor: int,
    n_scored: int,
    ruler_n: int,
) -> str:
    """This round's outcome stated in the numbers that decided it.

    Written whether the round was won or held. The election ranks θ-lift over the parent, so the
    sentence names that lift, both abilities and the SE behind them — the operator reading a
    lower-accuracy winner needs the number it actually won on, and a held round needs to say which
    arm came closest and how far short. On a COLD ruler it says so: θ there is logit-accuracy on
    each arm's own subset, which is not the scale the word promises."""
    from promptpotter.application.intelligence.exploration import theta_lift_over_parent

    scale = "" if ruler_n else " (cold ruler — θ is logit-accuracy on each arm's own subset)"
    parent = abilities.theta.get(PARENT_ABILITY_ID)
    census = f"{len(electable)} of {n_scored} electable, coverage floor {coverage_floor}"
    ranked = sorted(
        (
            (lift, cid)
            for cid in electable
            if (lift := theta_lift_over_parent(abilities, cid)) is not None
        ),
        reverse=True,
    )
    if not ranked:
        return f"no arm could be read against the parent on the round's δ ruler; {census}{scale}"
    if winner_id:
        lift = next(x for x, cid in ranked if cid == winner_id)
        runner = next(
            (
                f"; runner-up {labels.get(cid, cid[:12])} at {x:+.3f}"
                for x, cid in ranked
                if cid != winner_id
            ),
            "",
        )
        return (
            f"{labels.get(winner_id, winner_id[:12])} won on θ lift {lift:+.3f} "
            f"(θ {_theta(abilities.theta.get(winner_id))} vs parent {_theta(parent)}, "
            f"se {_theta(abilities.theta_se.get(winner_id), signed=False)}){runner}{scale}"
        )
    best_lift, best = ranked[0]
    return (
        f"no arm cleared the parent: best {labels.get(best, best[:12])} "
        f"θ {_theta(abilities.theta.get(best))} vs parent {_theta(parent)} "
        f"(lift {best_lift:+.3f}, se {_theta(abilities.theta_se.get(best), signed=False)}); "
        f"{census}{scale}"
    )


def _warn_if_not_separable(round_num: int, electable: list[ScoredCandidate]) -> None:
    """The round measured cleanly and resolved nothing — the one degradation that is SILENT on
    every other channel, because a winner was still crowned and every number still reads."""
    # Deliberately not a decision: the winner stands, and this only says the reader may not
    # treat the margin as a result. Silent when no arm carries an interval — below two shared
    # cells there is nothing to be inconclusive ABOUT.
    bracketed = [c for c in electable if c.matched_parent_lift_ci_lo is not None]
    if not bracketed or any(
        (c.matched_parent_lift_ci_lo or 0.0) > 0.0 or (c.matched_parent_lift_ci_hi or 0.0) < 0.0
        for c in bracketed
    ):
        return
    widest = max(bracketed, key=lambda c: c.matched_parent_lift_ci_hi or 0.0)
    emit_round_warning(
        kind="round_not_separable",
        message=(
            f"round {round_num} resolved nothing: every one of its {len(bracketed)} readable arms "
            f"has a lift interval spanning 0 (best reaches {widest.matched_parent_lift_ci_hi:+.3f} "
            "at its upper bound). A winner was still elected — read it as the best of what this "
            "round saw, not as a measured improvement over the origin"
        ),
        detail={"arms": len(bracketed), "best_ci_hi": widest.matched_parent_lift_ci_hi},
    )


async def l1_score(
    cycle: Cycle,
    candidates: list[CandidateProposal],
    dataset: list[Sample],
    *,
    pipeline_params: dict[str, Any] | None = None,
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
    # By identity, not position: `scored` below is filtered, so its index no longer aligns
    # with this full-population list.
    params_by_id = {
        ind.lineage.id: pp
        for ind, pp in zip(opt_sp_population, effective_pipeline_params, strict=True)
    }
    # THE cycle's decision sink, which `persist_round` flushes to the ledger AND folds into the
    # round file. A local list here reaches the round file only, leaving the declared sole
    # ingress never learning who won.
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
    # the union the candidates reached before elimination truncated them. Election comparisons
    # are unaffected either way (``matched_parent_stats`` restricts these rows per candidate);
    # what the narrow set corrupted is the round's published HEADLINE, which on a held round IS
    # this re-score, so it inherited the denominator of whatever PoBB cut. A round's headline
    # denominates over the round's panel or it is not a series.
    parent = await rescore_parent(cycle, dataset, round_num, callbacks=callbacks)
    # The round's scoring ends here, so an armed look-ahead is spent HERE rather than at the
    # round boundary — a press landing during critique waits for the next round instead of being
    # consumed having sped up nothing. Not in a `finally`: an unwound round did not score.
    # ONLY under `round` arming: a `batch` backend spends the press by the group it released, and
    # a second spender here would silently swallow one pressed after this round's last group.
    if (
        session.sample_lookahead_consume is not None
        and session.backend_client.concurrency_arming == "round"
    ):
        session.sample_lookahead_consume()
    # The shared comparison anchor for the θ election + paired diff. Its single-draw noise is
    # correlated across arms, so it floods every comparison equally rather than favouring one.
    parent_election_results: list[QueryMeasurement] = list(
        cast("list[QueryMeasurement]", parent.results)
    )
    # The no-winner headline: the RETAINED incumbent re-scored on this round's panel — one
    # configuration, on named samples, actually measured. Never publish `tracking.current_*`
    # here, which unions rows measured by DIFFERENT configurations and so reports a number no
    # individual scored. All nine are overwritten when a winner is elected.
    best_acc = parent.report.accuracy
    best_comp = parent.report.composite_fitness
    # Must share the headline's sample basis, or a held round renders a phantom lift. No winner
    # ⇒ both are the incumbent's standing; a winner keeps the matched reference set below.
    best_origin_accuracy = parent.report.accuracy
    best_opt_sp: OptSearchPoint = parent.opt_sp
    best_results: list[QueryMeasurement] = list(cast("list[QueryMeasurement]", parent.results))
    best_label = parent.report.label
    best_scores: dict[str, float] = dict(parent.report.evaluators)
    best_matched_parent_acc: float | None = parent.report.accuracy
    best_matched_parent_composite: float | None = parent.report.composite_fitness
    # Not seeded from the parent like its two neighbours: the parent's lift over ITSELF is 0 by
    # construction, and a round that crowned nobody publishing "+0.000" reads as a measured tie.
    best_lift: tuple[float | None, float | None, float | None] = (None, None, None)
    # Elect on confident improvement over the MATCHED origin, never raw accuracy vs origin's
    # full-set rate: elimination truncates candidates at different depths of one shared round
    # order, so the full-set rate is inflated by samples the candidate never reached.
    # ``elect_round_winner`` is the ONE election rule, shared with the resume divergence
    # replayer so a resumed run re-elects the same winner.
    # An under-probed candidate cannot win — a subset thinner than the elimination floor is too
    # noisy to trust over a fully-probed incumbent. Clamped so a tiny dataset stays electable.
    coverage_floor = min(pobb_config.n_min, len(dataset))
    # Composite + evaluators are computed ONCE in the scoring gateway and ride on the
    # `ScoredCandidate`; only the matched-origin floor is added here.
    cs_by_id = {cs.candidate_id: i for i, cs in enumerate(candidate_scores)}
    matched_by_id: dict[str, dict[str, Any] | None] = {}
    electable: list[str] = []
    for ind in scored:
        cs_idx = cs_by_id.get(ind.lineage.id)
        if cs_idx is None:
            continue
        cand_results = all_candidate_results[ind.lineage.id]
        # ``None`` unless this candidate covered the origin's whole panel: the round order is
        # stratified on the incumbent's grades, so the origin's rate on a truncated prefix is
        # decided by where PoBB stopped it, not by the data. A cut candidate's standing is its θ.
        matched = matched_parent_stats(
            cast("list[QueryMeasurement]", parent.results),
            cand_results,
            schema,
            round_scorer=session.scoring.round_scorer,
        )
        matched_by_id[ind.lineage.id] = matched
        # The floor and the INTERVAL on the lift over it are stamped together — a point estimate
        # without its error bar is what lets a six-cell panel read as a verdict. Unconditional
        # on ``matched``: the lift is defined on the cells both reached, so a truncated arm gets
        # an honest (wider) interval instead of nothing.
        lift = matched_parent_lift(cand_results, cast("list[QueryMeasurement]", parent.results))
        candidate_scores[cs_idx] = candidate_scores[cs_idx].model_copy(
            update={
                "matched_parent_accuracy": matched["accuracy"] if matched else None,
                "matched_parent_composite": matched["composite_fitness"] if matched else None,
                "matched_parent_lift": lift[0] if lift else None,
                "matched_parent_lift_ci_lo": lift[1] if lift else None,
                "matched_parent_lift_ci_hi": lift[2] if lift else None,
            }
        )
        # The admission rule, spelled once in the domain so the election and the ruler cannot
        # drift apart. Its collapse clause is why an arm can be scored here and still not enter:
        # a candidate answering ONE label to everything carries no measurement of ability, its
        # accuracy is decided by how much of that label the subset contains, and fitting θ to
        # that artifact drags the round below origin. It keeps its matched-origin stamp, so the
        # record stays honest; the round-level fact is charged to the L4 floor instead.
        if not is_electable(candidate_scores[cs_idx], cand_results):
            continue
        # …and the COVERAGE half of the same rule `elect_round_winner` applies below: an arm under
        # the floor cannot be crowned, so counting it electable overstates what could win. The
        # floor catches an arm thin for a reason OTHER than elimination (an operator skip) — PoBB
        # never cuts below its own `n_min`, which IS this floor.
        if distinct_valid_cells(cand_results) < coverage_floor:
            continue
        electable.append(ind.lineage.id)

    # This round's candidates are already banked, so the ruler's ≥2-arm floor is satisfiable NOW
    # — and the election below is what needs it. Warming only after ``absorb_round`` left every
    # election on a cold ruler, with no later round to spend a warm one on at L4's budget.
    # EXTENSION rides the same seam: on return the ruler covers every cell scored below, which is
    # what lets the θ fit raise on a hole instead of grading it δ=0.
    cycle.calibrate_ruler({**all_candidate_results, PARENT_ABILITY_ID: parent_election_results})
    # ``coverage_floor`` is persisted so the replayer applies the same electability floor;
    # without it a resumed run elects a thin candidate the live path rejected.
    winner_id, abilities = elect_round_winner(
        electable,
        all_candidate_results,
        parent_election_results,
        coverage_floor,
        cycle.ruler,
    )
    # From the SAME fixed-ruler fit the election just ran, never a second one, so the dashboard
    # can show *why* a lower-accuracy candidate won. ``None`` outside the election fit — and
    # ``None`` for EVERY candidate while the ruler is cold, since on a flat ruler θ degenerates
    # to logit-accuracy over the candidate's OWN subset and is not on the scale its name
    # promises. Guarded here rather than by emptying ``electable``, which is also the resume
    # decision's ``candidate_ids``; the election is still valid on a cold ruler.
    for cid in electable if cycle.ruler is not None else ():
        theta_c = abilities.theta.get(cid)
        if theta_c is None:
            continue
        cs_idx = cs_by_id[cid]
        cs = candidate_scores[cs_idx]
        # θ and its SE only — the whisker is NOT rewritten here. A second estimator writing the
        # same bounds reaches only the arms this loop reaches, so the band would appear and
        # vanish by election gating rather than by evidence. One band, stamped at
        # `candidate_scored`, is the whole mechanism.
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
        # The PARENT this election ranked against; the round document cannot carry it, since on a
        # won round `results` is the winner's rows.
        data={"parent_cells": parent_cells(parent_election_results)},
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
        best_matched_parent_acc = matched["accuracy"] if matched else None
        best_matched_parent_composite = matched["composite_fitness"] if matched else None
        best_lift = (
            winner_cs.matched_parent_lift,
            winner_cs.matched_parent_lift_ci_lo,
            winner_cs.matched_parent_lift_ci_hi,
        )

    base = _compute_accuracy(best_results)
    # The headline sample count rides the winner's ScoredCandidate row, so the round header
    # and the per-candidate table cannot disagree.
    best_total = winner_cs.total if winner_id else base["total"]
    p_value: float | None = None
    if base["total"] > 0 and winner_id:
        # A recorded diagnostic; it does not gate promotion. Significance runs on the per-sample
        # FITNESS rather than binary hits: a candidate lifting ground-truth's rank without yet
        # landing it at rank 1 is real improvement a binary-hit test is blind to.
        cand_fit, parent_fit = paired_fitness(best_results, parent_election_results)
        _d, _lo, _hi, p_value, _n = paired_reading(cand_fit, parent_fit, tail="greater")

    # A round improved iff it crowned somebody. ``elect_round_winner`` admits a candidate only on
    # a STRICTLY positive ability lift over the parent on the cycle's fixed δ ruler, so the
    # election already IS the gate. The second, stricter accuracy-recalibrated bar that used to sit
    # here gated nothing — ``absorb_round`` adopts the winner either way — so it could only ever
    # disagree with the adoption it annotated.
    improved = bool(winner_id)
    verdict_reason = _verdict_reason(
        winner_id=winner_id,
        electable=electable,
        abilities=abilities,
        labels={cs.candidate_id: cs.label for cs in candidate_scores},
        coverage_floor=coverage_floor,
        n_scored=len(scored),
        ruler_n=len(cycle.ruler.delta) if cycle.ruler is not None else 0,
    )
    round_result = RoundResult(
        round=round_num,
        label=best_label,
        accuracy=best_acc,
        composite_fitness=best_comp,
        total=best_total,
        improved=improved,
        p_value=p_value,
        verdict_reason=verdict_reason,
        origin_accuracy=best_origin_accuracy,
        matched_parent_accuracy=best_matched_parent_acc,
        matched_parent_composite=best_matched_parent_composite,
        matched_parent_lift=best_lift[0],
        matched_parent_lift_ci_lo=best_lift[1],
        matched_parent_lift_ci_hi=best_lift[2],
        prompt_fields={
            **best_opt_sp.prompt_field_dict(),
            "lineage": best_opt_sp.lineage.model_dump(),
        },
        # Stripped, because the round's incoming params carry the PREVIOUS winner's render and
        # nothing re-renders at this write — persisting it makes the document claim a prompt the
        # winner never ran. `prompt_fields` above is the winner's own, and every reader rebuilds
        # the render from it through `to_job_search_point`.
        pipeline_params=strip_rendered_prompt(
            params_by_id.get(winner_id, pipeline_params) if winner_id else pipeline_params
        ),
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
        # `candidate_scores`, and passing them would be a second recording that could disagree.
        l1_parse_failure=yield_stats.l1_parse_failure,
        # Stamped here rather than at save time: a re-save (a repair, a rescore) must not
        # restamp a round with the optimizer running NOW and erase which one actually ran it.
        optimizer_prompt_hashes=compute_optimizer_prompt_hashes(),
    )
    return round_result, best_opt_sp


__all__ = ["l1_score"]
