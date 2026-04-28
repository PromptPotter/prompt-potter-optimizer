"""L1 round execution — generate → score+select → critique → persist to memory."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.nodes.formatting import candidate_summaries
from promptpotter.application.optimization.nodes.l1.critique import (
    format_l1_critique_for_prompt,
    run_l1_critique,
)
from promptpotter.application.optimization.nodes.l1.generate import l1_generate
from promptpotter.application.optimization.nodes.l1.measure import (
    L1YieldStats,
    detect_invariants,
)
from promptpotter.application.optimization.nodes.l1.score import l1_score
from promptpotter.domain.phases import CampaignPhase, emit_phase
from promptpotter.domain.results import CandidateProposal, RoundResult

# Module-level import for test monkeypatching.
from promptpotter.infrastructure.llm import client as _llm_client
from promptpotter.infrastructure.tracing import observed_node
from promptpotter.infrastructure.tracing.events import (
    L1CritiqueWritten,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign.runner import RunListener
    from promptpotter.application.optimization.elimination import DegradationCheck
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = ["execute_round"]


async def _generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> tuple[list[CandidateProposal], L1YieldStats]:
    """Load persisted candidates or generate fresh ones via LLM; detect no-op + duplicate variants."""
    session = cycle.session
    config = cycle.config
    # Cap n_variants at 3× config so L2 can't blow up the round budget.
    opt = config.optimization
    model = config.optimizer_llm.model
    opt_params = cycle.opt_sp.optimizer_params
    _n_variants = min(opt_params.get("n_variants", opt.n_variants), opt.n_variants * 3)
    _creativity = opt_params.get("creativity", opt.creativity)
    prompt_preview = cycle.opt_sp.render()[:120]

    assert cycle.current_sp is not None
    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "enter",
        round=round_num,
        current_accuracy=cycle.current_accuracy,
        prompt_preview=prompt_preview,
        n_variants=_n_variants,
        creativity=_creativity,
        model=model or "(default)",
        has_l1_critique=bool(cycle.opt_sp.l1_critique_text),
        pipeline_params=cycle.current_sp.pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
    )

    if session.state.cycle_id:
        persisted_raw = session.store.campaigns.load_round_candidates(
            session.backend_id,
            session.state.cycle_id,
            round_num,
        )
        if persisted_raw is not None:
            persisted = [CandidateProposal.model_validate(d) for d in persisted_raw]
            logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
            yield_stats = detect_invariants(persisted, cycle.opt_sp)
            # llm_call never fires on this branch — synthesize l1_generate
            # so dashboard.json + round_NNNN.json don't miss the node.
            if _rr := session.state.round_recorder:
                _rr.set_node(
                    "l1_generate",
                    {
                        "input": {"source": "loaded_from_disk", "round": round_num},
                        "output": {"candidates": candidate_summaries(persisted)},
                    },
                )
            emit_phase(
                on_phase,
                CampaignPhase.L1_GENERATE,
                "exit",
                round=round_num,
                n_candidates=len(persisted),
                n_eval_queries=n_eval_queries,
                loaded_from_disk=True,
                candidates=candidate_summaries(persisted),
                l1_yield=yield_stats.yield_,
                l1_n_no_op=yield_stats.n_no_op,
                l1_n_duplicate=yield_stats.n_duplicate,
            )
            return persisted, yield_stats

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    client = _llm_client.get_llm_client(config.optimizer_llm.provider)
    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=session.state.obs_campaign_id,
        round_num=round_num,
    ):
        candidates = await l1_generate(
            cycle,
            n_variants=_n_variants,
            creativity=_creativity,
            llm_client=client,
            model=model,
            obs=obs,
            round_num=round_num,
        )

    yield_stats = detect_invariants(candidates, cycle.opt_sp)

    if session.state.cycle_id:
        session.store.campaigns.save_round_candidates(
            session.backend_id,
            session.state.cycle_id,
            round_num,
            [cp.model_dump() for cp in candidates],
        )

    emit_phase(
        on_phase,
        CampaignPhase.L1_GENERATE,
        "exit",
        round=round_num,
        n_candidates=len(candidates),
        n_eval_queries=n_eval_queries,
        loaded_from_disk=False,
        candidates=candidate_summaries(candidates),
        l1_yield=yield_stats.yield_,
        l1_n_no_op=yield_stats.n_no_op,
        l1_n_duplicate=yield_stats.n_duplicate,
    )

    return candidates, yield_stats


async def execute_round(
    cycle: Cycle,
    round_num: int,
    scoring_dataset: list[Sample],
    callbacks: RunListener,
    degradation_checks: list[DegradationCheck] | None = None,
) -> RoundResult:
    """Execute one L1 round: generate → score+select → critique → persist to memory."""
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    obs = session.state.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.state.obs_campaign_id, round_num=round_num))

    candidates, yield_stats = await _generate_or_load_candidates(
        round_num, cycle, callbacks.on_phase, n_eval_queries=len(scoring_dataset), obs=obs
    )

    assert cycle.current_sp is not None
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_queries=len(scoring_dataset),
        current_best_accuracy=cycle.current_accuracy,
        improvement_threshold=opt.improvement_threshold,
        current_pipeline_params=cycle.current_sp.pipeline_params,
    )
    baseline = cycle.baseline_for_round(scoring_dataset, round_num)
    async with observed_node(
        f"l1_score_r{round_num}",
        "scoring",
        obs=obs,
        obs_type="span",
        campaign_id=session.state.obs_campaign_id,
        round_num=round_num,
    ):
        scoring_result = await l1_score(
            cycle,
            candidates,
            scoring_dataset,
            baseline,
            pipeline_params=cycle.current_sp.pipeline_params,
            improvement_threshold=opt.improvement_threshold,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            elimination_n_min=opt.elimination_n_min,
            elimination_alpha=opt.elimination_alpha,
            round_num=round_num,
            yield_stats=yield_stats,
        )
        if obs and scoring_result.candidate_scores:
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=str(scoring_result.winner_prompt_fields.get("id") or ""),
                    winner_accuracy=scoring_result.winner_accuracy,
                    improved=scoring_result.improved,
                )
    candidate_scores_dicts = [cs.to_dict() for cs in scoring_result.candidate_scores]
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=scoring_result.label,
        winner_accuracy=scoring_result.winner_accuracy,
        winner_composite=scoring_result.winner_composite,
        winner_evaluators=dict(scoring_result.winner_evaluators),
        improved=scoring_result.improved,
        candidate_scores=candidate_scores_dicts,
    )

    critique_text = ""
    if scoring_result.winner_results:
        crit_llm = _llm_client.get_llm_client(config.optimizer_llm.provider)
        critique_result = await run_l1_critique(
            cycle,
            scoring_result,
            session.pipeline_schema,
            crit_llm,
            round_num=round_num,
            model=config.optimizer_llm.model,
            recorder=session.state.round_recorder,
        )
        critique_text = format_l1_critique_for_prompt(critique_result)
    if obs and critique_text:
        with graceful("L1CritiqueWritten emit failed"):
            obs.emit_write_point(
                L1CritiqueWritten,
                campaign_id=session.state.obs_campaign_id,
                round_num=round_num,
                l1_critique_text=critique_text,
            )

    cycle.apply_round_outcome(scoring_result, critique_text)

    round_result = RoundResult(
        round=round_num,
        label=scoring_result.label,
        accuracy=scoring_result.winner_accuracy,
        composite=scoring_result.winner_composite,
        hits=scoring_result.hits,
        total=scoring_result.total,
        improved=scoring_result.improved,
        prompt_fields=scoring_result.winner_prompt_fields,
        pipeline_params=scoring_result.winner_pipeline_params,
        results=scoring_result.winner_results,
        all_candidate_results=dict(scoring_result.all_candidate_results),
        candidates_scored=scoring_result.candidates_scored,
        candidate_scores=candidate_scores_dicts,
        decisions=[d.to_dict() for d in scoring_result.decisions],
        degraded_queries=scoring_result.degraded_queries,
        deprecated=scoring_result.deprecated,
        escalation_signal=scoring_result.escalation_signal,
        evaluators=scoring_result.winner_evaluators,
        l1_yield=scoring_result.l1_yield,
        l1_n_no_op=scoring_result.l1_n_no_op,
        l1_n_duplicate=scoring_result.l1_n_duplicate,
    )

    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    accuracy=scoring_result.winner_accuracy,
                    hits=scoring_result.hits,
                    total=scoring_result.total,
                    improved=scoring_result.improved,
                    winner_prompt_fields_id=scoring_result.winner_prompt_fields.get("id", ""),
                    candidate_scores=candidate_scores_dicts,
                    model=config.optimizer_llm.model or "",
                    n_variants=config.optimization.n_variants,
                    optimizer_templates=["l1_generate", "l1_critique"],
                    evaluators=dict(scoring_result.winner_evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            w_osp = scoring_result.winner_osp
            obs.emit(
                PromptVersion(
                    campaign_id=session.state.obs_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=w_osp.lineage.id,
                    rendered_prompt=w_osp.render(),
                    layer1_fields={f: getattr(w_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=w_osp.lineage.parent_id,
                )
            )

    return round_result
