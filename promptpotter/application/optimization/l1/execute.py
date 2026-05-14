"""L1 round driver — top-level entry called by the runner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch.hub import format_l1_critique_for_prompt
from promptpotter.application.optimization.l1.critique import run_l1_critique
from promptpotter.application.optimization.l1.resume import generate_or_load_candidates
from promptpotter.application.optimization.l1.score import l1_score
from promptpotter.application.optimization.pobb.elimination import PoBBConfig
from promptpotter.application.optimization.round_analysis import compute_round_diagnostics
from promptpotter.config.settings import PROMPT_STRING_FIELDS
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.results import RoundResult
from promptpotter.domain.validators import StopRule

# Module-level alias for test monkeypatching.
from promptpotter.infrastructure import llm as _llm_client
from promptpotter.infrastructure.tracing import (
    L1CritiqueWritten,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
    observed_node,
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
    scoring_set: list[Sample],
    callbacks: RunCallbacks,
    degradation_checks: list[StopRule] | None = None,
    *,
    skip_critique: bool = False,
) -> RoundResult:
    """Execute one L1 round: generate → score+select → critique. Returns
    the ``RoundResult`` with ``.critique`` set when L1_CRITIQUE fired; the
    runner calls ``cycle.absorb_round`` to fold it into Cycle state at the
    boundary. l1.py never mutates Cycle directly.

    ``skip_critique=True`` skips the round-end ``run_l1_critique`` LLM call.
    Sweep mode passes this so the cheap-round_data fork stays one full live LLM
    call (round 2 generate-only); the round-1 critique would otherwise dwarf
    that call and is the same across all forks since round 1 is identical.
    """
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    obs = session.state.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.tracing_campaign_id, round_num=round_num))

    candidates, yield_stats = await generate_or_load_candidates(
        round_num, cycle, callbacks.on_phase, n_eval_queries=len(scoring_set), obs=obs
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
    origin = cycle.origin_for_round(scoring_set, round_num)
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
            origin,
            pipeline_params=cycle.tracking.current_sp.pipeline_params,
            improvement_threshold=opt.improvement_threshold,
            improvement_significance=opt.improvement_significance,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            pobb_config=PoBBConfig(
                n_min=opt.elimination_n_min,
                epsilon=opt.pobb_epsilon,
                lock_in=opt.pobb_lock_in,
                lock_in_n_min=opt.pobb_lock_in_n_min,
            ),
            round_num=round_num,
            yield_stats=yield_stats,
        )
        if obs and round_result.candidate_scores:
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.state.tracing_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=str(round_result.prompt_fields.get("id") or ""),
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
        candidate_scores=round_result.candidate_scores,
    )

    # Compute deterministic post-scoring stats once and attach to the round
    # result. The dispatch hub's ``diagnostics`` signal reads this; rendering
    # is layer-agnostic so the same payload feeds L1_CRITIQUE / L2 / L3.
    # ``cycle.probe_next_round`` is still True at this point — runner only
    # resets it after ``absorb_round`` lands. Reading ``best_accuracy`` here
    # is the pre-probe full-set best (probe didn't fold yet).
    rounds_history = [*cycle.rounds, round_result]
    prior_full_accuracy = cycle.tracking.best_accuracy if cycle.probe_next_round else 0.0
    round_result.diagnostics = compute_round_diagnostics(
        round_result,
        rounds_history,
        session.pipeline_schema,
        prompt_chars=len(cycle.opt_sp.render()),
        probe_just_completed=cycle.probe_next_round,
        axis_tested=cycle.last_l2_axis,
        prior_full_accuracy=prior_full_accuracy,
    )

    critique_text = ""
    if round_result.results and not skip_critique:
        crit_llm = _llm_client.get_llm_client(config.optimizer_llm.provider)
        # Critique is round-over-round feedback; survive a malformed LLM
        # response rather than crash the campaign.
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
                    session.pipeline_schema,
                    crit_llm,
                    round_num=round_num,
                    model=config.optimizer_llm.model,
                    ledger=session.state.ledger,
                )
            round_result.critique = critique_result
            critique_text = format_l1_critique_for_prompt(critique_result)
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
                    winner_prompt_fields_id=round_result.prompt_fields.get("id", ""),
                    candidate_scores=round_result.candidate_scores,
                    model=config.optimizer_llm.model or "",
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
