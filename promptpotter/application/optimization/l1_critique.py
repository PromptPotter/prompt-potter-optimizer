"""L1-critique LLM call. Template references signals directly via the dispatch hub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch_hub import (
    DispatchHub,
    Layer,
    build_bundle,
)
from promptpotter.application.optimization.llm_call import run_optimizer_node
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm import LLMClientBase

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.projections import AuditTrailProjection

logger = logging.getLogger(__name__)

__all__ = [
    "format_l1_critique_for_prompt",
    "run_l1_critique",
]


async def run_l1_critique(
    cycle: Cycle,
    round_result: RoundResult,
    schema: PipelineSchema | None,
    llm_client: LLMClientBase,
    *,
    round_num: int,
    model: str | None = None,
    recorder: AuditTrailProjection | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict.

    L1-critique has no per-section override channel: the template body
    embeds ``{{diagnostics}}``, ``{{failures}}``, ``{{plan}}``,
    ``{{l2_directive}}`` and ``{{critique}}`` placeholders that the
    dispatch hub resolves to layer-agnostic signal renderers.
    ``RoundDiagnostics`` is read off the freshly-completed round, so the
    rank/health/evolution context is one bundle hop away.
    """
    bundle = build_bundle(Layer.L1_CRITIQUE, cycle, latest_round=round_result)
    from promptpotter.application.optimization.llm_call import load_optimizer_prompt

    template = load_optimizer_prompt("l1_critique")
    prompt_vars = DispatchHub.fill_fixed(template, bundle)

    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=prompt_vars,
        llm_client=llm_client,
        model=model,
        recorder=recorder,
        optimizer_call_cache=cycle.session.store.optimizer_calls,
    )
    logger.info(
        "L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        round_num + 1,
        round_result.accuracy,
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
