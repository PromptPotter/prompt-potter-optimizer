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


async def run_feedback_cycle(
    instruction: str,
    eval_data: list[dict[str, Any]],
    config: CycleConfig,
    *,
    improvement_areas: str = "",
) -> CycleResult:
    """Run iterative optimization with feedback cycling.

    Executes:
    1. InitNode — create baseline PromptState
    2. Loop: GrowFilterNode → AnalysisEvalNode → route by next_action
    3. Stop when patience exhausted, max_rounds reached, or perfect score

    Returns:
        CycleResult with all rounds and final winner.
    """
    from api.nodes.optimizer_nodes import (
        AnalysisEvalNode,
        GrowFilterNode,
        InitNode,
    )

    started_at = datetime.now(timezone.utc).isoformat()

    # -- Step 1: Initialize baseline --
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
    current_results: list = []

    rounds: list[CycleRoundResult] = []
    best_accuracy = 0.0
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
            },
        )
        eval_out = await eval_node.process({
            "candidates": grow_out.candidates,
            "eval_data": eval_data,
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
            candidates_evaluated=eval_out.winner.get("candidates_evaluated", 0),
            candidate_scores=eval_out.candidate_scores,
        )
        rounds.append(round_result)

        # Update current best
        current_ps = eval_out.winner_prompt_state
        current_accuracy = eval_out.winner_accuracy

        if current_accuracy > best_accuracy:
            best_accuracy = current_accuracy
            best_round = round_num
            best_ps = current_ps

        # Track stalls
        if eval_out.improved:
            stall_count = 0
        else:
            stall_count += 1

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

    return CycleResult(
        rounds=rounds,
        n_rounds=len(rounds),
        best_accuracy=best_accuracy,
        best_round=best_round,
        baseline_accuracy=rounds[0].accuracy if rounds else 0.0,
        winner_prompt_state=best_ps,
        stop_reason=stop_reason,
        started_at=started_at,
        finished_at=finished_at,
    )
