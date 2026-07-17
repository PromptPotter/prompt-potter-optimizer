"""L1 round driver — top-level entry called by the runner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.intelligence.exploration import (
    build_observations,
    select_round_subset,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import optimizer_model
from promptpotter.application.optimization.l1.critique import run_l1_critique
from promptpotter.application.optimization.l1.resume import generate_or_load_candidates
from promptpotter.application.optimization.l1.score.winner import l1_score
from promptpotter.application.optimization.pobb.checks import PoBBConfig
from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.rendering import format_l1_critique_for_prompt
from promptpotter.domain.results import RoundResult
from promptpotter.domain.validators import StopRule

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure.tracing.bridge import observed_node
from promptpotter.infrastructure.tracing.events import (
    L1CritiqueWritten,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.domain.sample import Sample

logger = logging.getLogger(__name__)


async def execute_round(
    cycle: Cycle,
    round_num: int,
    scoring_pool: list[Sample],
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    *,
    skip_critique: bool = False,
    is_final_round: bool = False,
) -> RoundResult:
    """Execute one L1 round: generate → score+select → critique. Returns a
    ``RoundResult`` (`.critique` set when L1_CRITIQUE fired); runner folds it
    into Cycle state via ``absorb_round`` — l1.py never mutates Cycle directly.

    ``skip_critique=True`` (sweep mode) drops the round-end critique so the
    cheap-fork stays one LLM call; round 1 is identical across all forks.

    ``is_final_round=True`` (the loop's calendar cap is about to bite) likewise drops
    it — see the critique site for why a critique nobody will read is pure latency.
    """
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    obs = session.state.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.tracing_campaign_id, round_num=round_num))

    # Probe rounds use their warned-query set as-is; non-probe rounds narrow
    # the train-split bank to ``sp_budget_ttest`` contested samples via the
    # adaptive queue mechanism. Origin + every candidate share this subset so PoBB compares
    # like-for-like.
    if cycle.probe_next_round:
        scoring_set = scoring_pool
    elif not opt.mechanisms.selection.per_round_resubset or not cycle.delta_scale:
        # Frozen selection: ignore accumulating observations, so every round gets the
        # deterministic campaign-start subset (bank prefix) — one fixed sample basis.
        # Two triggers: resubset is OFF, OR the δ ruler is still COLD (empty) — a
        # per-round subset would then be difficulty-blind and cross-round-incomparable,
        # and freezing concentrates measurements so the ruler warms + locks fastest
        # (`Cycle._maybe_warm_ruler`). Once warm, the branch below thaws to adaptive.
        scoring_set = select_round_subset(scoring_pool, [], config.sp_budget_ttest)
    else:
        # Archive obs are dataset-scoped + abort-residue-free → cross-cycle evidence.
        observations = [*cycle.archive_observations, *build_observations(cycle.rounds)]
        scoring_set = select_round_subset(
            scoring_pool,
            observations,
            config.sp_budget_ttest,
        )

    candidates, yield_stats = await generate_or_load_candidates(
        round_num, cycle, callbacks.on_phase, n_scoring_samples=len(scoring_set), obs=obs
    )

    assert cycle.tracking.current_sp is not None
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_samples=len(scoring_set),
        current_best_accuracy=cycle.tracking.current_accuracy,
        improvement_threshold=opt.improvement_threshold,
        current_pipeline_params=cycle.tracking.current_sp.pipeline_params,
    )
    async with observed_node(
        f"l1_score_r{round_num}",
        "scoring",
        obs=obs,
        as_type="span",
        campaign_id=session.state.tracing_campaign_id,
        round_num=round_num,
    ):
        round_result, winner_osp = await l1_score(
            cycle,
            candidates,
            scoring_set,
            pipeline_params=cycle.tracking.current_sp.pipeline_params,
            improvement_threshold=opt.improvement_threshold,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            pobb_config=PoBBConfig(
                n_min=opt.elimination_n_min,
                epsilon=opt.pobb_epsilon,
                lock_in=opt.pobb_lock_in,
                lock_in_n_min=opt.pobb_lock_in_n_min,
                epsilon_elimination=opt.mechanisms.elimination.epsilon_elimination,
                leader_lock_in=opt.mechanisms.elimination.leader_lock_in,
                margin_elimination=opt.mechanisms.elimination.margin_elimination,
                improvement_threshold=opt.improvement_threshold,
            ),
            round_num=round_num,
            yield_stats=yield_stats,
        )
        # The elected winner IS the round's resulting incumbent — and on a HELD round
        # (no candidate cleared the floor) l1_score returns the retained incumbent itself
        # (origin.osp), so the ids match and absorb_round adopts nothing. absorb reads
        # this to advance the cycle's identity to the winner on an advancing round.
        round_result.opt_search_point = winner_osp
        if obs and round_result.candidate_scores:
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=winner_osp.lineage.id,
                    winner_accuracy=round_result.accuracy,
                    improved=round_result.improved,
                )
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=round_result.label,
        winner_accuracy=round_result.accuracy,
        winner_composite_fitness=round_result.composite_fitness,
        winner_evaluators=dict(round_result.evaluators),
        winner_hits=round_result.hits,
        winner_total=round_result.total,
        improved=round_result.improved,
        improved_reason=round_result.improved_reason,
        p_value=round_result.p_value,
        candidate_scores=[c.model_dump() for c in round_result.candidate_scores],
        winner_matched_origin_accuracy=round_result.matched_origin_accuracy,
        winner_matched_origin_hits=round_result.matched_origin_hits,
        winner_matched_origin_composite=round_result.matched_origin_composite,
    )

    # Round diagnostics feed dispatch's ``diagnostics`` signal (L1_CRITIQUE/L2/L3).
    # ``probe_next_round`` is still True here — runner resets it after
    # ``absorb_round``, so ``best_accuracy`` is the pre-probe full-set best.
    rounds_history = [*cycle.rounds, round_result]
    prior_full_accuracy = cycle.tracking.best_accuracy if cycle.probe_next_round else 0.0
    round_result.diagnostics = compute_round_diagnostics(
        round_result,
        rounds_history,
        session.pipeline_schema,
        probe_just_completed=cycle.probe_next_round,
        axis_tested=cycle.last_l2_axis,
        prior_full_accuracy=prior_full_accuracy,
    )

    critique_text = ""
    # A zero-candidate round (l1_generate returned [] — empty/parse-failed provider
    # response) leaves ``round_result.results`` holding the ORIGIN's rows, so without the
    # ``candidates`` guard critique would fire a meta LLM call to critique nothing. Skip it:
    # the empty generation is already recorded as a ``ValidationFailure`` wound that routes
    # L2 to heal l1_generate — that is the right next move, not another critique.
    # Critique is feedback FOR THE NEXT ROUND's l1_generate — nothing else reads it. When no
    # next round will run, the call is pure latency: on the L4 inner benchmark the terminal
    # critique + the terminal L2 fire together burned 15.8% of all inner wall time producing
    # output that died with the cycle. Both boundaries must be checked: the calendar cap
    # (`is_final_round`, known before the round) and the lives bank emptying (knowable only
    # now, from this round's `improved` verdict — asked through the FSM so the lookahead can
    # never disagree with the banking `post_round` is about to do).
    will_stop = is_final_round or cycle.escalation.would_exhaust_lives(
        round_result.improved, config.optimization.lives
    )
    if candidates and round_result.results and not skip_critique and not will_stop:
        # Critique is round-over-round feedback — survive a malformed response.
        with graceful("L1 critique failed; continuing without round-over-round feedback"):
            async with observed_node(
                f"l1_critique_r{round_num}",
                "llm/meta",
                obs=obs,
                campaign_id=session.state.tracing_campaign_id,
                round_num=round_num,
            ):
                critique_result = await run_l1_critique(
                    cycle,
                    round_result,
                    round_num=round_num,
                    ledger=session.state.ledger,
                )
            round_result.critique = critique_result
            critique_text = format_l1_critique_for_prompt(critique_result, session.pipeline_schema)
    if obs and critique_text:
        with graceful("L1CritiqueWritten emit failed"):
            obs.emit_write_point(
                L1CritiqueWritten,
                campaign_id=session.state.tracing_campaign_id,
                round_num=round_num,
                l1_critique_text=critique_text,
            )

    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    accuracy=round_result.accuracy,
                    hits=round_result.hits,
                    total=round_result.total,
                    improved=round_result.improved,
                    winner_prompt_fields_id=winner_osp.lineage.id,
                    candidate_scores=[c.model_dump() for c in round_result.candidate_scores],
                    model=optimizer_model(),
                    n_variants=config.optimization.n_variants,
                    optimizer_templates=["l1_generate", "l1_critique"],
                    evaluators=dict(round_result.evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=winner_osp.lineage.id,
                    rendered_prompt=winner_osp.render(),
                    layer1_fields={f: getattr(winner_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=winner_osp.lineage.parent_id,
                )
            )

    return round_result


__all__ = ["execute_round"]
