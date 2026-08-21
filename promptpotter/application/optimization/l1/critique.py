from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from promptpotter.application.optimization.dispatch.facade import (
    DispatchHub,
    build_bundle,
    injection_char_counts,
    injection_coverage_counts,
    injection_silent_panels,
)
from promptpotter.application.optimization.dispatch.llm_call.call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.llm_call.prompts import resolve_node_layout
from promptpotter.application.optimization.dispatch.schemas import L1CritiqueOutput
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
    *,
    round_num: int,
    ledger: CycleEventLog | None = None,
) -> CritiqueReadout:
    """Build the critique from pipeline stats + LLM analysis. The output is materialized to a dict so persistence does not
    drag Pydantic into the domain serialization path."""
    bundle = build_bundle(cycle, latest_round=round_result)
    from promptpotter.application.optimization.dispatch.llm_call.prompts import (
        load_optimizer_prompt,
    )

    template, prompt_vars, rendered, coverage = DispatchHub.fill(
        load_optimizer_prompt("l1_critique"),
        resolve_node_layout("l1_critique"),
        bundle,
        node="l1_critique",
    )

    result, _prompt, _repairs = await run_optimizer_node(
        template_name="l1_critique",
        prompt_vars=prompt_vars,
        template=template,
        context=LLMCallContext(
            ledger=ledger,
            round_num=round_num,
            cache=cycle.session.store.optimizer_reuse,
            injection_chars=injection_char_counts(rendered, prompt_vars),
            injection_dropped=injection_coverage_counts(coverage),
            injection_silent=tuple(injection_silent_panels(coverage)),
        ),
    )
    assert isinstance(result, L1CritiqueOutput), (
        f"l1_critique must return L1CritiqueOutput, got {type(result).__name__}"
    )
    return cast(CritiqueReadout, result.model_dump())
