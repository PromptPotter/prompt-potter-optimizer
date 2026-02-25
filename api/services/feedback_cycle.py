"""
Feedback cycling orchestrator for iterative prompt optimization.

Wraps optimizer nodes (InitNode → GrowFilterNode → AnalysisEvalNode) in a
counter-based loop with 3-path routing based on ``next_action``:

- **generate** (Layer 1): vary persona, instruction, thinking_style, etc.
- **refine_context** (Layer 2): adjust context and parameters
- **modify_plan** (Layer 3): change the overall strategy/plan
- **stop**: terminate the loop

Stopping conditions:
- ``max_rounds`` reached
- ``patience`` consecutive non-improving rounds exhausted
- ``next_action == "stop"`` from analysis node
- ``winner_accuracy >= 1.0`` (perfect score)
"""

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CycleConfig(BaseModel):
    """Configuration for feedback cycling."""

    max_rounds: int = Field(10, description="Maximum optimization rounds")
    patience: int = Field(3, description="Stop after N consecutive non-improvements")
    n_variants: int = Field(5, description="Candidates per round")
    creativity: float = Field(0.7, description="Temperature for candidate generation")
    improvement_threshold: float = Field(0.01, description="Min accuracy delta")
    model: str | None = Field(None, description="LLM model identifier")
    provider: str | None = Field(None, description="LLM provider")
    backend_url: str = Field(..., description="Backend URL for evaluation")
    backend_id: str = Field("", description="Backend identifier for caching")
    project_root: str = Field("", description="Project root for store")
    generate_suggestions: bool = Field(False, description="LLM suggestions each round")
    pipeline_params: dict | None = Field(None, description="Pipeline parameter overrides")
    session_terms: list[str] | None = Field(None, description="Backend session terms")
    temperature: float = Field(0.0, description="Temperature for content hash")
    queries_per_eval: int = Field(0, description="Subsample size (0 = use all)")
    seed: int = Field(42, description="Random seed for subsampling")


class CycleRoundResult(BaseModel):
    """Result of a single feedback cycle round."""

    round: int
    label: str
    accuracy: float
    hits: int
    total: int
    improved: bool
    next_action: str
    prompt_state: dict
    results: list[dict] = Field(default_factory=list)
    candidates_evaluated: int
    candidate_scores: list[dict] = Field(default_factory=list)


class CycleResult(BaseModel):
    """Final result of the feedback cycling process."""

    rounds: list[CycleRoundResult]
    n_rounds: int
    best_accuracy: float
    best_round: int
    baseline_accuracy: float
    winner_prompt_state: dict
    stop_reason: str
    started_at: str
    finished_at: str
    langfuse_trace_id: str | None = None


async def run_feedback_cycle(
    instruction: str,
    eval_data: list[dict[str, Any]],
    config: CycleConfig,
    *,
    improvement_areas: str = "",
    baseline_prompt_state: dict | None = None,
    baseline_accuracy: float = 0.0,
    baseline_results: list | None = None,
    on_round_complete: Callable | None = None,
    on_candidate_eval: Callable | None = None,
    on_query_eval: Callable | None = None,
    langfuse_session_id: str | None = None,
) -> CycleResult:
    """Run iterative optimization with feedback cycling.

    Executes:
    1. InitNode — create baseline PromptState (skipped if baseline_prompt_state provided)
    2. Loop: GrowFilterNode → AnalysisEvalNode → route by next_action
    3. Stop when patience exhausted, max_rounds reached, or perfect score

    Args:
        instruction: Raw prompt instruction text (used by InitNode if no baseline).
        eval_data: Evaluation dataset (list of query dicts).
        config: Cycle configuration.
        improvement_areas: Domain-expert guidance for context restructuring.
        baseline_prompt_state: Existing baseline PromptState (serialized dict).
            If provided, InitNode is skipped and this is used as the starting point.
        baseline_accuracy: Accuracy of the baseline (used when baseline_prompt_state
            is provided).
        baseline_results: Eval results for the baseline (for failure analysis).
        on_round_complete: Optional callback after each round:
            ``(round_result: CycleRoundResult, stall_count: int)``
        on_candidate_eval: Optional callback after each candidate evaluation:
            ``(candidate_idx: int, n_candidates: int, scores: dict)``
        langfuse_session_id: Optional session ID for grouping Langfuse traces.

    Returns:
        CycleResult with all rounds and final winner.
    """
    import random as _random

    from api.nodes.optimizer_nodes import (
        AnalysisEvalNode,
        GrowFilterNode,
        InitNode,
    )
    from api.services.backend_client import BackendClient
    from api.services.langfuse_client import LangfuseLogger
    from api.services.observability_logger import ObsLogger

    langfuse = LangfuseLogger.get_instance()
    started_at = datetime.now(timezone.utc).isoformat()

    # File-based observability (parallel to cloud Langfuse)
    obs: ObsLogger | None = None
    if config.project_root and config.backend_id:
        try:
            obs = ObsLogger(config.project_root, config.backend_id)
        except Exception:
            logger.debug("Failed to create ObsLogger", exc_info=True)

    # Initialize backend session (required before /matches calls)
    if config.session_terms:
        bc = BackendClient(config.backend_url)
        await bc.init_session(config.session_terms)

    # Subsample eval data if configured
    if config.queries_per_eval > 0 and len(eval_data) > config.queries_per_eval:
        rng = _random.Random(config.seed)
        round_eval_data = rng.sample(eval_data, config.queries_per_eval)
    else:
        round_eval_data = eval_data

    # Create campaign-level trace
    campaign_trace_id = langfuse.create_trace(
        name="feedback_cycle",
        input={"instruction": instruction, "max_rounds": config.max_rounds},
        metadata={"config": config.model_dump()},
        session_id=langfuse_session_id,
        tags=["campaign", "feedback_cycle"],
    )

    # File-based campaign trace
    obs_campaign_id = campaign_trace_id or f"campaign_{started_at[:19].replace(':', '')}"
    if obs:
        try:
            obs.log_campaign_start(
                campaign_id=obs_campaign_id,
                config=config.model_dump(),
                baseline_accuracy=baseline_accuracy,
            )
        except Exception:
            logger.debug("ObsLogger.log_campaign_start failed", exc_info=True)

    # -- Step 1: Initialize baseline --
    if baseline_prompt_state is not None:
        # Use provided baseline — skip InitNode
        current_ps = baseline_prompt_state
        current_accuracy = baseline_accuracy
        current_results: list = baseline_results or []
        logger.info(
            "Using provided baseline (acc=%.3f)", current_accuracy,
        )
    else:
        # Run InitNode to create baseline from instruction
        init_node = InitNode(
            node_id="cycle_init",
            config={"model": config.model, "provider": config.provider},
        )
        init_out = await init_node.process({
            "instruction": instruction,
            "improvement_areas": improvement_areas,
        })
        current_ps = init_out.prompt_state
        current_accuracy = 0.0
        current_results = []

    rounds: list[CycleRoundResult] = []
    best_accuracy = current_accuracy
    best_round = -1
    best_ps = current_ps
    stall_count = 0
    stop_reason = "max_rounds"

    # -- Step 2: Iterative loop --
    for round_num in range(config.max_rounds):
        logger.info(
            "Feedback cycle round %d (acc=%.3f, stall=%d/%d)",
            round_num, current_accuracy, stall_count, config.patience,
        )

        # Grow: generate candidates
        grow_node = GrowFilterNode(
            node_id=f"cycle_grow_{round_num}",
            config={
                "model": config.model,
                "provider": config.provider,
                "n_variants": config.n_variants,
                "creativity": config.creativity,
            },
        )
        grow_out = await grow_node.process({
            "prompt_state": current_ps,
            "accuracy": current_accuracy,
            "results": current_results,
        })

        # Evaluate: score candidates and select winner
        eval_node = AnalysisEvalNode(
            node_id=f"cycle_eval_{round_num}",
            config={
                "model": config.model,
                "provider": config.provider,
                "backend_url": config.backend_url,
                "backend_id": config.backend_id,
                "project_root": config.project_root,
                "improvement_threshold": config.improvement_threshold,
                "generate_suggestions": config.generate_suggestions,
                "pipeline_params": config.pipeline_params,
                "temperature": config.temperature,
                "on_candidate_eval": on_candidate_eval,
                "on_query_eval": on_query_eval,
            },
        )
        eval_out = await eval_node.process({
            "candidates": grow_out.candidates,
            "eval_data": round_eval_data,
            "baseline_prompt_state": current_ps,
            "baseline_accuracy": current_accuracy,
            "baseline_results": current_results,
            "baseline_label": f"round_{round_num}" if round_num > 0 else "baseline",
        })

        # Record round
        round_result = CycleRoundResult(
            round=round_num,
            label=eval_out.winner.get("label", f"round_{round_num}"),
            accuracy=eval_out.winner_accuracy,
            hits=eval_out.winner.get("hits", 0),
            total=eval_out.winner.get("total", 0),
            improved=eval_out.improved,
            next_action=eval_out.next_action,
            prompt_state=eval_out.winner_prompt_state,
            results=eval_out.winner_results,
            candidates_evaluated=eval_out.winner.get("candidates_evaluated", 0),
            candidate_scores=eval_out.candidate_scores,
        )
        rounds.append(round_result)

        # Log per-round Langfuse trace with accuracy score
        if campaign_trace_id:
            langfuse.create_span(
                trace_id=campaign_trace_id,
                name=f"round_{round_num}",
                input={
                    "n_candidates": len(grow_out.candidates),
                    "baseline_accuracy": current_accuracy,
                },
                output={
                    "winner_accuracy": eval_out.winner_accuracy,
                    "improved": eval_out.improved,
                    "next_action": eval_out.next_action,
                },
                metadata={
                    "round": round_num,
                    "candidates_evaluated": eval_out.winner.get(
                        "candidates_evaluated", 0,
                    ),
                },
            )
            langfuse.create_score(
                trace_id=campaign_trace_id,
                name=f"accuracy_round_{round_num}",
                value=eval_out.winner_accuracy,
                comment=f"Round {round_num}: "
                        f"{'improved' if eval_out.improved else 'no change'}",
            )

        # File-based round + prompt logging
        if obs:
            try:
                obs.log_round(
                    campaign_id=obs_campaign_id,
                    round_num=round_num,
                    accuracy=eval_out.winner_accuracy,
                    hits=eval_out.winner.get("hits", 0),
                    total=eval_out.winner.get("total", 0),
                    improved=eval_out.improved,
                    next_action=eval_out.next_action,
                    winner_prompt_state_id=eval_out.winner_prompt_state.get(
                        "id", "",
                    ),
                    candidate_scores=eval_out.candidate_scores,
                    model=config.model or "",
                    temperature=config.temperature,
                    n_variants=config.n_variants,
                )
                # Log prompt version for the winner
                from api.models.prompt_state import PromptState
                winner_ps = PromptState(**eval_out.winner_prompt_state)
                obs.log_prompt_version(
                    prompt_state_id=winner_ps.id,
                    rendered_prompt=winner_ps.render(),
                    layer1_fields={
                        f: getattr(winner_ps, f)
                        for f in [
                            "persona", "task_intent", "problem_description",
                            "instruction", "thinking_style", "answer_format",
                        ]
                    },
                    parent_id=winner_ps.parent_id,
                )
            except Exception:
                logger.debug("ObsLogger round/prompt logging failed", exc_info=True)

        # Update current state — pass results forward for failure analysis
        current_ps = eval_out.winner_prompt_state
        current_accuracy = eval_out.winner_accuracy
        current_results = eval_out.winner_results

        if current_accuracy > best_accuracy:
            best_accuracy = current_accuracy
            best_round = round_num
            best_ps = current_ps

        # Track stalls
        if eval_out.improved:
            stall_count = 0
        else:
            stall_count += 1

        # Notify callback
        if on_round_complete:
            on_round_complete(round_result, stall_count)

        # Check stopping conditions
        if current_accuracy >= 1.0:
            stop_reason = "perfect_score"
            logger.info("Perfect score reached at round %d", round_num)
            break

        if stall_count >= config.patience:
            stop_reason = "patience_exhausted"
            logger.info(
                "Patience exhausted after %d stalls at round %d",
                stall_count, round_num,
            )
            break

        if eval_out.next_action == "stop":
            stop_reason = "next_action_stop"
            logger.info("Analysis node signaled stop at round %d", round_num)
            break

    finished_at = datetime.now(timezone.utc).isoformat()

    # Update campaign trace with final results
    if campaign_trace_id:
        langfuse.create_score(
            trace_id=campaign_trace_id,
            name="best_accuracy",
            value=best_accuracy,
            comment=f"Best at round {best_round}, stop: {stop_reason}",
        )
        langfuse.update_trace(
            trace_id=campaign_trace_id,
            output={
                "best_accuracy": best_accuracy,
                "n_rounds": len(rounds),
                "stop_reason": stop_reason,
            },
            metadata={"stop_reason": stop_reason, "best_round": best_round},
        )
        langfuse.flush()

    return CycleResult(
        rounds=rounds,
        n_rounds=len(rounds),
        best_accuracy=best_accuracy,
        best_round=best_round,
        baseline_accuracy=rounds[0].accuracy if rounds else current_accuracy,
        winner_prompt_state=best_ps,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
        langfuse_trace_id=campaign_trace_id,
    )
