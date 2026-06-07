"""L1-critique LLM call. Template references signals directly via the dispatch hub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.dispatch.hub import (
    DispatchHub,
    build_bundle,
)
from promptpotter.application.optimization.dispatch.llm_call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import L1CritiqueOutput
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import CritiqueReadout

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
    *,
    round_num: int,
    ledger: CycleEventLog | None = None,
) -> CritiqueReadout:
    """Build critique from pipeline stats + LLM analysis; returns dict for storage.

    Template embeds ``{{plan|task_context|diagnostics|validation_failures|runtime_failures}}``
    placeholders resolved by the dispatch hub. ``L1CritiqueOutput`` materialized to
    dict so persistence doesn't drag Pydantic into the domain serialization path.
    """
    bundle = build_bundle(cycle, latest_round=round_result)
    from promptpotter.application.optimization.dispatch.llm_call import load_optimizer_prompt

    template = load_optimizer_prompt("l1_critique")
    prompt_vars = DispatchHub.fill_fixed(template, bundle)

    result, prompt = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=prompt_vars,
        context=LLMCallContext(
            ledger=ledger,
            round_num=round_num,
            cache=cycle.session.store.optimizer_calls,
        ),
    )
    assert isinstance(result, L1CritiqueOutput), (
        f"l1_critique must return L1CritiqueOutput, got {type(result).__name__}"
    )
    logger.info(
        "L1 critique: %d chars prompt, round %d, acc=%.3f",
        len(prompt),
        round_num,
        round_result.accuracy,
    )
    return cast(CritiqueReadout, result.model_dump())
