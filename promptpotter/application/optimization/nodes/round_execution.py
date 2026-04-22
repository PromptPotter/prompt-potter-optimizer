"""Round execution — generate, evaluate, select winner, update state."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.campaign.callbacks import RunListener
from promptpotter.application.optimization.cycle import Cycle
from promptpotter.application.optimization.nodes.critique import (
    CritiqueAgent,
    format_critique_for_prompt,
    sample_thinking_styles,
)
from promptpotter.application.optimization.nodes.critique_payload import (
    RoundSnapshot,
    update_query_tracker,
)
from promptpotter.application.optimization.nodes.formatting import candidate_summaries
from promptpotter.application.optimization.phases import CampaignPhase, emit_phase
from promptpotter.application.optimization.results import RoundResult
from promptpotter.application.scoring.metrics import compute_composite_score

# Module-level import for test monkeypatching.
from promptpotter.infrastructure.llm import client as _llm_client
from promptpotter.infrastructure.tracing import observed_node
from promptpotter.infrastructure.tracing.events import (
    CritiqueWritten,
    PromptVersion,
    RoundEnd,
    RoundStart,
    RoundWinnerChosen,
)
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.optimization.nodes.escalation import DegradationCheck
    from promptpotter.application.optimization.nodes.score import L1ScoringResult
    from promptpotter.domain.sample import Sample
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = ["PauseForReviewError", "execute_round"]


class PauseForReviewError(Exception):
    """Raised when HITL mode pauses the loop for human/AI review."""

    def __init__(
        self,
        candidates: list[dict],
        round_num: int,
        pause_point: str,
    ) -> None:
        self.candidates = candidates
        self.round_num = round_num
        self.pause_point = pause_point  # CampaignPhase.L1_GENERATE, "before_l2_eval", "user_pause"
        super().__init__(
            f"Paused at {pause_point}: {len(candidates)} candidates (round {round_num})"
        )


async def _generate_or_load_candidates(
    round_num: int,
    cycle: Cycle,
    on_phase=None,
    n_eval_queries: int = 0,
    *,
    obs: ObservabilityBridge | None = None,
) -> list[dict]:
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
        has_critique=bool(cycle.opt_sp.memory.critique_text),
        pipeline_params=cycle.current_sp.pipeline_params,
        parent_prompt_fields={k: v for k, v in cycle.opt_sp.prompt_field_dict().items() if v},
    )

    if session.campaign_store and session.cycle_id:
        persisted = session.campaign_store.load_round_candidates(
            session.backend_id,
            session.cycle_id,
            round_num,
        )
        if persisted is not None:
            logger.debug(
                "Loaded %d persisted candidates for round %d",
                len(persisted),
                round_num,
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

    from promptpotter.application.optimization.nodes.generate import l1_generate

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

    if session.campaign_store and session.cycle_id:
        session.campaign_store.save_round_candidates(
            session.backend_id,
            session.cycle_id,
            round_num,
            candidates,
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


async def _run_critique(
    scoring_result: L1ScoringResult,
    round_num: int,
    cycle: Cycle,
) -> str:
    """Run critique analysis on evaluation results. Returns formatted critique text."""
    config = cycle.config
    if not config.optimization.enable_critique or not scoring_result.winner_results:
        return ""

    crit_llm = _llm_client.get_llm_client()
    agent = CritiqueAgent(crit_llm, model=config.optimizer_llm.model)

    _CRITIQUE_SM_KEYS = frozenset(
        {
            "discriminating_queries",
            "failure_clusters",
            "tractability",
            "exhausted_axes",
            "value_trends",
            "improvement_attribution",
        }
    )
    cctx = RoundSnapshot.from_round_state(
        cycle,
        scoring_result,
        config,
        cycle.session.pipeline_schema,
        round_num=round_num,
        search_memory_digest=(
            cycle.search_memory.digest(_CRITIQUE_SM_KEYS) if cycle.search_memory else None
        ),
    )
    result = await agent.run(cctx)
    return format_critique_for_prompt(result)


async def _score_and_select(
    candidates: list[dict],
    round_num: int,
    cycle: Cycle,
    scoring_dataset: list[Sample],
    callbacks: RunListener,
    obs: ObservabilityBridge | None = None,
    degradation_checks: list[DegradationCheck] | None = None,
) -> L1ScoringResult:
    """Evaluate candidates, run critique, select winner."""
    from promptpotter.application.optimization.nodes.score import l1_score

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

    # Probe rounds: subset baseline to probe's query set; else every probe looks like a regression.
    _baseline_acc = cycle.current_accuracy
    _baseline_comp = cycle.current_composite
    _baseline_results = cycle.current_results
    if cycle.probe_next_round and cycle.current_results and session.pipeline_schema:
        from typing import cast

        from promptpotter.domain.scoring import QueryResult

        _probe_queries = {s.query for s in scoring_dataset}
        _subset = [r for r in cycle.current_results if r.get("query") in _probe_queries]
        if _subset:
            _subset_scores = compute_composite_score(
                cast(list[QueryResult], _subset),
                session.pipeline_schema,
                round_scorer=session.round_scorer,
            )
            _baseline_acc = _subset_scores["accuracy"]
            _baseline_comp = _subset_scores.get("composite", _baseline_acc)
            _baseline_results = _subset

    current_best = {
        "accuracy": _baseline_acc,
        "composite": _baseline_comp,
        "prompt_fields": cycle.opt_sp.prompt_field_dict(),
        "results": _baseline_results,
        "label": f"round_{round_num}" if round_num > 0 else "baseline",
    }

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
            current_best,
            pipeline_params=cycle.current_sp.pipeline_params,
            improvement_threshold=opt.improvement_threshold,
            callbacks=callbacks,
            degradation_checks=degradation_checks,
            elimination_n_min=opt.elimination_n_min,
            elimination_alpha=opt.elimination_alpha,
            round_num=round_num,
        )

        if obs and scoring_result.candidate_scores:
            winner_id = str(scoring_result.winner_prompt_fields.get("id", ""))
            with graceful("RoundWinnerChosen emit failed"):
                obs.emit_write_point(
                    RoundWinnerChosen,
                    campaign_id=session.obs_campaign_id,
                    round_num=round_num,
                    winner_candidate_id=winner_id,
                    winner_accuracy=scoring_result.winner_accuracy,
                    improved=scoring_result.improved,
                )

        scoring_result.critique_text = await _run_critique(scoring_result, round_num, cycle)
        scoring_result.thinking_styles = sample_thinking_styles(n=3, seed=opt.seed + round_num + 1)
        if obs and scoring_result.critique_text:
            with graceful("CritiqueWritten emit failed"):
                obs.emit_write_point(
                    CritiqueWritten,
                    campaign_id=session.obs_campaign_id,
                    round_num=round_num,
                    critique_text=scoring_result.critique_text,
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
        critique_text=scoring_result.critique_text,
    )

    return scoring_result


async def execute_round(
    cycle: Cycle,
    round_num: int,
    scoring_dataset: list[Sample],
    callbacks: RunListener,
    degradation_checks: list[DegradationCheck] | None = None,
) -> RoundResult:
    """Execute one optimization round: generate → evaluate → select winner → obs log."""
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

    if config.optimization.pause_before_scoring:
        raise PauseForReviewError(candidates, round_num, pause_point="before_scoring")

    scoring_result = await _score_and_select(
        candidates,
        round_num,
        cycle,
        scoring_dataset,
        callbacks,
        obs=obs,
        degradation_checks=degradation_checks,
    )

    cycle.opt_sp.memory.critique_text = scoring_result.critique_text
    cycle.opt_sp.memory.thinking_styles = scoring_result.thinking_styles

    if scoring_result.winner_results and session.pipeline_schema:
        from promptpotter.application.scoring.metrics import compile_failure_analysis

        cycle.opt_sp.memory.failure_analysis = compile_failure_analysis(
            scoring_result.winner_results,
            session.pipeline_schema,
        )
    else:
        cycle.opt_sp.memory.failure_analysis = None

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

    # Warning inventory spans ALL candidate results — aborted candidates carry warnings.
    _all_results: list = [r for rs in scoring_result.all_candidate_results.values() for r in rs]
    if _all_results:
        update_query_tracker(cycle.opt_sp.memory.warning_inventory, _all_results)

    # Mirror per-candidate RuntimeFailures onto outer opt_sp (dedup by source/warning/config)
    # so L2 sees accumulated evidence across rounds; L3 replans when L2 can't reduce.
    from promptpotter.domain.analysis import RuntimeFailure

    def _rf_key(rf_dict: dict) -> tuple:
        cfg = rf_dict.get("observed_config") or {}
        return (
            rf_dict.get("source", ""),
            rf_dict.get("dominant_warning", ""),
            tuple(sorted(cfg.items())),
        )

    existing_keys = {_rf_key(rf.to_dict()) for rf in cycle.opt_sp.memory.runtime_failures}
    for cs in scoring_result.candidate_scores:
        for rf_dict in cs.get("runtime_failures") or []:
            k = _rf_key(rf_dict)
            if k in existing_keys:
                continue
            existing_keys.add(k)
            cycle.opt_sp.memory.runtime_failures.append(RuntimeFailure(**rf_dict))

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
                    optimizer_templates=["meta_scan_aware", "critique"],
                    evaluators=dict(scoring_result.winner_evaluators),
                )
            )
        with graceful("PromptVersion emit failed"):
            w_osp = scoring_result.winner_osp
            obs.emit(
                PromptVersion(
                    campaign_id=session.obs_campaign_id,
                    round_num=round_num,
                    prompt_fields_id=w_osp.id,
                    rendered_prompt=w_osp.render(),
                    layer1_fields={f: getattr(w_osp, f) for f in PROMPT_STRING_FIELDS},
                    parent_id=w_osp.parent_id,
                )
            )

    return round_result
