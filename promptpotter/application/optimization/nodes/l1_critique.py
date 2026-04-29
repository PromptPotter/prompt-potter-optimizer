"""L1 critique phase — LLM analysis of a round's results."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.nodes.dispatch_msg_registry import (
    Layer,
    assemble_dispatch_msg,
)
from promptpotter.application.optimization.pipeline import run_optimizer_node

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.llm.client import LLMClientBase
    from promptpotter.infrastructure.persistence.round_recorder import RoundRecorder

    from .l1_score import L1ScoringResult

logger = logging.getLogger(__name__)

__all__ = [
    "format_l1_critique_for_prompt",
    "run_l1_critique",
]


async def run_l1_critique(
    cycle: Cycle,
    scoring_result: L1ScoringResult,
    schema: PipelineSchema | None,
    llm_client: LLMClientBase,
    *,
    round_num: int,
    model: str | None = None,
    recorder: RoundRecorder | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict."""
    dispatch_msg = assemble_dispatch_msg(
        Layer.L1_CRITIQUE,
        cycle,
        round_num=round_num,
        scoring_result=scoring_result,
        pipeline_schema=schema,
    )
    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        compile_vars={"dispatch_msg": dispatch_msg},
        llm_client=llm_client,
        model=model,
        recorder=recorder,
    )
    logger.info(
        "Rich L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        round_num + 1,
        scoring_result.winner_accuracy,
    )
    return result


def format_l1_critique_for_prompt(critique: dict) -> str:
    """L1 critique dict → compact text for L1/L2 (summary + priority_fix + axes + highlights)."""
    parts = []
    if critique.get("summary"):
        parts.append(critique["summary"])
    if critique.get("priority_fix"):
        parts.append(f"Priority fix: {critique['priority_fix']}")
    if critique.get("suggested_axes"):
        parts.append(f"Suggested axes: {', '.join(critique['suggested_axes'])}")
    highlights = critique.get("failure_highlights", [])
    if highlights:
        parts.append("Key failures:")
        for h in highlights[:5]:
            parts.append(f"  {h}")
    return "\n".join(parts)
