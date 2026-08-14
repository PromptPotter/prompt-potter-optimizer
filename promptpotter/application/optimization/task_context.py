"""Task check-in: raw context → L1 prompt fields + task_context, cached as ``task_context.yaml``. Read and
write do NOT share a tier — read resolves tenant-then-install, a decomposition always lands in tenant."""

from __future__ import annotations

import json
from typing import Any

from promptpotter.application.optimization.dispatch.llm_call.call import (
    LLMCallContext,
    run_optimizer_node,
)
from promptpotter.application.optimization.dispatch.schemas import CheckinOutput
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.store.dataset_access import (
    readable_task_context,
)
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.tracing.bridge import observed_node

__all__ = [
    "checkin_call_context",
    "committed_task_context",
    "decompose_prompt_fields",
]


def committed_task_context(stores: Stores, dataset_name: str | None) -> TaskDecomposition:
    """The framing check-in committed, read PURELY — the half IDENTITY may use, since a
    decomposition needs a cycle to bill to and a mint is computing that cycle."""
    if dataset_name is None:
        return TaskDecomposition()
    existing = readable_task_context(stores, dataset_name)
    if not existing:
        return TaskDecomposition()
    task_context = TaskDecomposition.from_dict(existing)
    # The budget is enforced HERE too, not only on the async path: whichever seam reads the
    # framing first is the one that must refuse an over-budget field, or the clip lands on
    # every render for the run's whole life.
    task_context.check_budget(source=f"{dataset_name}/task_context.yaml")
    return task_context


def checkin_call_context(stores: Stores, hop: CycleHop) -> LLMCallContext:
    """The check-in call's audit home — the seeded campaign's own cycle ledger. The cache is what makes an unchanged
    decomposition free on replay; omitting it silently re-spends."""
    return LLMCallContext(
        ledger=CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop))),
        round_num=0,
        cache=stores.optimizer_reuse,
    )


async def decompose_prompt_fields(
    context_input: Any, *, campaign_id: str, context: LLMCallContext
) -> dict[str, Any]:
    """LLM check-in: raw context → Layer 1 prompt fields + task_context. Provider and model come from the ``checkin``
    optimizer node config, resolved inside :func:`llm_call`."""
    if isinstance(context_input, dict):
        user_content = (
            "The user has provided partial Layer 1 fields for a prompt. "
            "Validate them, fill any gaps, and suggest improvements.\n\n"
            f"Provided fields:\n{json.dumps(context_input, indent=2)}"
        )
    else:
        user_content = (
            "The user has provided a raw context description. Parse it into "
            "structured Layer 1 prompt fields.\n\n"
            f"Context:\n{context_input}"
        )

    consultation_instruction = (
        "Return a JSON object with exactly these keys. Use empty string for "
        "fields that don't apply. Be concise and actionable."
    )

    token = set_cycle_ledger(context.ledger)
    try:
        async with observed_node(
            "checkin",
            "llm/optimizer",
            obs=None,
            campaign_id=campaign_id,
            round_num=0,
        ):
            result, _, _ = await run_optimizer_node(
                template_name="checkin",
                prompt_vars={"consultation_instruction": consultation_instruction},
                user_content=user_content,
                context=context,
            )
    finally:
        reset_cycle_ledger(token)

    assert isinstance(result, CheckinOutput), (
        f"checkin must return CheckinOutput, got {type(result).__name__}"
    )
    # Pydantic guarantees every Layer-1 + task_context field is present
    # (defaults to empty string on the model). Materialize to dict for
    # downstream consumers that pre-date the typed boundary.
    return result.model_dump()
