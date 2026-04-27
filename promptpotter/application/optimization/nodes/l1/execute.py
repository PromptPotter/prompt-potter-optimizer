"""L1 round execution — generate → score+select → critique → persist to memory."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.nodes.formatting import candidate_summaries
from promptpotter.application.optimization.nodes.l1.critique import (
    RoundSnapshot,
    format_l1_critique_for_prompt,
    run_l1_critique,
)
from promptpotter.application.optimization.nodes.l1.generate import l1_generate
from promptpotter.application.optimization.nodes.l1.score import l1_score
from promptpotter.application.optimization.pipeline import get_round_recorder
from promptpotter.application.optimization.results import CandidateProposal, RoundResult
from promptpotter.domain.phases import CampaignPhase, emit_phase

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
    from promptpotter.application.optimization.nodes.l1.score import L1ScoringResult
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = ["PauseForReviewError", "execute_round"]


class PauseForReviewError(Exception):
    """Raised when HITL mode pauses the loop at the after_round checkpoint."""

    def __init__(self, candidates: list[dict], round_num: int) -> None:
        self.candidates = candidates
        self.round_num = round_num
        super().__init__(f"Paused: {len(candidates)} candidates (round {round_num})")


async def _generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> list[CandidateProposal]:
    """Load persisted candidates or generate fresh ones via LLM."""
    session = cycle.session
    config = cycle.config
    # Cap n_variants at 3× config so L2 can't blow up eval budget.
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

    if session.cycle_id:
        persisted_raw = session.store.campaigns.load_round_candidates(
            session.backend_id,
            session.cycle_id,
            round_num,
        )
        if persisted_raw is not None:
            persisted = [CandidateProposal.model_validate(d) for d in persisted_raw]
            logger.debug("Loaded %d persisted candidates for round %d", len(persisted), round_num)
            # llm_call never fires on this branch — synthesize l1_generate
            # so dashboard.json + round_NNNN.json don't miss the node.
            if _rr := get_round_recorder():
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
            )
            return persisted

    logger.debug("No persisted candidates for round %d — generating fresh", round_num)

    client = _llm_client.get_llm_client()
    async with observed_node(
        f"l1_generate_r{round_num}",
        "llm/meta",
        obs=obs,
        campaign_id=session.obs_campaign_id,
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

    if session.cycle_id:
        session.store.campaigns.save_round_candidates(
            session.backend_id,
            session.cycle_id,
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
    )

    return candidates


async def _score_and_select(
    candidates: list[CandidateProposal],
    round_num: int,
    cycle: Cycle,
    scoring_dataset: list[Sample],
    callbacks: RunListener,
    obs: ObservabilityBridge | None = None,
    degradation_checks: list[DegradationCheck] | None = None,
) -> L1ScoringResult:
    """Evaluate candidates and select winner."""
    session = cycle.session
    config = cycle.config
    opt = config.optimization
    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "enter",
        round=round_num,
        n_candidates=len(candidates),
        n_queries=len(scoring_dataset),
        current_best_accuracy=cycle.current_accuracy,
        improvement_threshold=opt.improvement_threshold,
        current_pipeline_params=cycle.current_sp.pipeline_params if cycle.current_sp else None,
    )

    baseline = cycle.baseline_for_round(scoring_dataset, round_num)

    async with observed_node(
        f"l1_score_r{round_num}",
        "scoring",
        obs=obs,
        obs_type="span",
        campaign_id=session.obs_campaign_id,
        round_num=round_num,
    ):
        assert cycle.current_sp is not None
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
        )

        if obs and scoring_result.candidate_scores:
            winner_id = str(scoring_result.winner_prompt_fields.get("id") or "")
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.obs_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=winner_id,
                    winner_accuracy=scoring_result.winner_accuracy,
                    improved=scoring_result.improved,
                )

    emit_phase(
        callbacks.on_phase,
        CampaignPhase.L1_SCORE,
        "exit",
        round=round_num,
        winner_label=scoring_result.label,
        winner_accuracy=scoring_result.winner_accuracy,
        winner_composite=scoring_result.winner_composite,
        improved=scoring_result.improved,
        candidate_scores=scoring_result.candidate_scores,
    )

    return scoring_result


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
    obs = session.obs
    if obs:
        with graceful("RoundStart emit failed"):
            obs.emit(RoundStart(campaign_id=session.obs_campaign_id, round_num=round_num))

    candidates = await _generate_or_load_candidates(
        round_num,
        cycle,
        callbacks.on_phase,
        n_eval_queries=len(scoring_dataset),
        obs=obs,
    )

    scoring_result = await _score_and_select(
        candidates,
        round_num,
        cycle,
        scoring_dataset,
        callbacks,
        obs=obs,
        degradation_checks=degradation_checks,
    )

    critique_text = ""
    if scoring_result.winner_results:
        crit_llm = _llm_client.get_llm_client()
        cctx = RoundSnapshot.from_round_state(
            cycle,
            scoring_result,
            config,
            session.pipeline_schema,
            round_num=round_num,
            search_memory_digest=(
                cycle.search_memory.digest_for_l1_critique() if cycle.search_memory else None
            ),
        )
        critique_result = await run_l1_critique(cctx, crit_llm, model=config.optimizer_llm.model)
        critique_text = format_l1_critique_for_prompt(critique_result)
    if obs and critique_text:
        with graceful("L1CritiqueWritten emit failed"):
            obs.emit_write_point(
                L1CritiqueWritten,
                campaign_id=session.obs_campaign_id,
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
        candidate_scores=scoring_result.candidate_scores,
        decisions=list(scoring_result.decisions),
        degraded_queries=scoring_result.degraded_queries,
        escalation_signal=scoring_result.escalation_signal,
        evaluators=scoring_result.winner_evaluators,
    )

    if obs:
        with graceful("RoundEnd emit failed"):
            obs.emit(
                RoundEnd(
                    campaign_id=session.obs_campaign_id,
                    round_num=round_num,
                    accuracy=scoring_result.winner_accuracy,
                    hits=scoring_result.hits,
                    total=scoring_result.total,
                    improved=scoring_result.improved,
                    winner_prompt_fields_id=scoring_result.winner_prompt_fields.get("id", ""),
                    candidate_scores=scoring_result.candidate_scores,
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
                    campaign_id=session.obs_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=w_osp.lineage.id,
                    rendered_prompt=w_osp.render(),
                    layer1_fields={f: getattr(w_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=w_osp.lineage.parent_id,
                )
            )

    return round_result
