"""L1-critique LLM call. Template references signals directly via the dispatch hub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.dispatch_hub import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimization.llm_call import run_optimizer_node
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm import LLMClientBase

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.domain.results import RoundResult
    from promptpotter.infrastructure.ledger import CycleEventLog

logger = logging.getLogger(__name__)

__all__ = [
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
    ledger: CycleEventLog | None = None,
) -> dict:
    """Build critique from pipeline stats + LLM analysis. Returns the raw 6-field LLM dict.

    L1-critique has no per-section override channel: the template body
    embeds ``{{plan}}``, ``{{task_context}}``, ``{{diagnostics}}``,
    ``{{validation_failures}}`` and ``{{runtime_failures}}`` placeholders
    that the dispatch hub resolves to layer-agnostic signal renderers.
    ``RoundDiagnostics`` is read off the freshly-completed round, so the
    rank/health/evolution context is one bundle hop away.
    """
    bundle = build_bundle(cycle, latest_round=round_result)
    from promptpotter.application.optimization.llm_call import load_optimizer_prompt

    template = load_optimizer_prompt("l1_critique")
    prompt_vars = DispatchHub.fill_fixed(template, bundle)

    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=prompt_vars,
        llm_client=llm_client,
        model=model,
        ledger=ledger,
        round_num=round_num,
        optimizer_call_cache=cycle.session.store.optimizer_calls,
    )
    logger.info(
        "L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        round_num + 1,
        round_result.accuracy,
    )
    return result
