"""Task-description check-in: raw context → Layer-1 prompt fields + task_context.

One LLM decomposition, cached on disk as ``task_context.yaml``. The web ingest path
writes that file at commit (from the check-in it already ran);
:func:`load_or_build_task_context` is the single run-start seam that reads it — or
decomposes a benchmark's ``task_description.md`` once on first sight. No second
check-in call recomputes what ingest already produced.

**Read and write do not share a tier.** The framing resolves tenant-then-install
(``store/dataset_access.py::readable_task_context``), but a first-sight decomposition
is persisted to the TENANT tree, never beside the definition it was derived from —
that dir is inside ``site-packages`` under a wheel.

The call bills the campaign it seeds. :func:`checkin_call_context` names that cycle,
and *context* is required at both seams precisely so no caller can reach the LLM
without one: this decomposition ran unbilled and untraced — the ledger binds
``_CYCLE_LEDGER`` (token/cost, via ``emit_token_usage``) *and* rides ``context``
(the audit ``LLMCallRecord``), which are two channels, not one.
"""

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
    readable_dataset_dir,
    readable_task_context,
)
from promptpotter.infrastructure.store.io import read_text_optional
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.infrastructure.tracing.bridge import observed_node

__all__ = [
    "checkin_call_context",
    "decompose_prompt_fields",
    "load_or_build_task_context",
]


def checkin_call_context(stores: Stores, hop: CycleHop) -> LLMCallContext:
    """The check-in call's audit home — the seeded campaign's own cycle ledger.

    The cache is what makes an unchanged decomposition free on replay; omitting it
    silently re-spends (see :class:`LLMCallContext`)."""
    return LLMCallContext(
        ledger=CycleEventLog.open(CycleDir(stores.campaigns.cycle_dir(hop))),
        round_num=0,
        cache=stores.optimizer_calls,
    )


async def decompose_prompt_fields(
    context_input: Any, *, campaign_id: str, context: LLMCallContext
) -> dict[str, Any]:
    """LLM check-in: raw context → Layer 1 prompt fields + task_context sub-dict.

    Provider + model come from the ``checkin`` optimizer node config
    (``promptpotter/assets/optimizer/pipeline.yaml``), resolved inside :func:`llm_call`."""
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


async def load_or_build_task_context(
    stores: Stores,
    dataset_name: str | None,
    *,
    campaign_id: str,
    context: LLMCallContext,
) -> TaskDecomposition:
    """Run-start task framing — resolve the dataset's ``task_context.yaml``, or
    decompose ``task_description.md`` once on first sight and persist it.

    The single seam both the web mint path and CLI ``new`` funnel through: an
    ingested dataset already carries ``task_context.yaml`` (written at commit from
    the check-in's decomposition), so the run reads it with no LLM call. A benchmark
    that shipped without one has only ``task_description.md`` — decompose it once and
    every later run is a free read. Empty framing when neither exists.

    Takes the store and the NAME, not a resolved dir, because the read and the write
    do not share a tier. The framing resolves through
    :func:`~promptpotter.infrastructure.store.dataset_access.readable_task_context`
    (tenant, then install) while the write always lands in the tenant tree: the
    decomposition is derived from install content but is not install content, and
    under a wheel that dir is inside ``site-packages``. Handed a dir, this function
    wrote back into whichever tier it was handed — which is exactly what it must not
    do for the four benchmarks that ship no ``task_context.yaml``.
    """
    if dataset_name is None:
        return TaskDecomposition()
    existing = readable_task_context(stores, dataset_name)
    if existing:
        task_context = TaskDecomposition.from_dict(existing)
        # The budget is enforced HERE — once, before the campaign starts — and nowhere else.
        # Clipping at render instead amputates an over-budget field on every prompt for the
        # run's whole life, visible only as a log line.
        task_context.check_budget(source=f"{dataset_name}/task_context.yaml")
        return task_context
    task_description = read_text_optional(
        readable_dataset_dir(stores, dataset_name) / "task_description.md"
    )
    if not task_description:
        return TaskDecomposition()
    result = await decompose_prompt_fields(
        task_description, campaign_id=campaign_id, context=context
    )
    tc_dict = dict(result["task_context"])
    tc_dict["raw_description"] = task_description
    task_context = TaskDecomposition.from_dict(tc_dict)
    # Persist BEFORE gating: the decomposition call is already paid for, so a verbose checkin
    # leaves an editable file behind rather than burning the call and vanishing.
    path = stores.tenant_datasets.save_task_context(dataset_name, task_context.to_dict())
    task_context.check_budget(source=str(path))
    return task_context
