"""Task-description check-in: raw context → Layer-1 prompt fields + task_context.

One LLM decomposition, cached on disk as the dataset's ``task_context.json``.
The web ingest path writes that file at commit (from the check-in it already ran);
:func:`load_or_build_task_context` is the single run-start seam that reads it — or
decomposes a repo benchmark's ``task_description.md`` once on first sight. No second
check-in call recomputes what ingest already produced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from promptpotter.application.optimization.dispatch.llm_call import run_optimizer_node
from promptpotter.application.optimization.dispatch.schemas import CheckinOutput
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.infrastructure.store.base import (
    read_json_optional,
    read_text_optional,
    write_json,
)

__all__ = [
    "decompose_prompt_fields",
    "load_or_build_task_context",
]


async def decompose_prompt_fields(context_input: Any) -> dict[str, Any]:
    """LLM check-in: raw context → Layer 1 prompt fields + task_context sub-dict.

    Provider + model come from the ``checkin`` optimizer node config
    (``datasets/_optimizer/pipeline.json``), resolved inside :func:`llm_call`."""
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

    result, _, _ = await run_optimizer_node(
        template_name="checkin",
        prompt_vars={"consultation_instruction": consultation_instruction},
        user_content=user_content,
    )
    assert isinstance(result, CheckinOutput), (
        f"checkin must return CheckinOutput, got {type(result).__name__}"
    )
    # Pydantic guarantees every Layer-1 + task_context field is present
    # (defaults to empty string on the model). Materialize to dict for
    # downstream consumers that pre-date the typed boundary.
    return result.model_dump()


async def load_or_build_task_context(
    dataset_config_dir: Path | None,
) -> TaskDecomposition:
    """Run-start task framing — read the committed ``{dir}/task_context.json``, or
    decompose ``task_description.md`` once on first sight and persist it.

    The single seam both the web mint path and CLI ``new`` funnel through: an
    ingested dataset already carries ``task_context.json`` (written at commit from
    the check-in's decomposition), so the run reads it with no LLM call. A repo
    benchmark / pre-change dataset has only ``task_description.md`` — decompose it
    once, write the file, and every later run is a free read. Empty framing when
    neither file exists.
    """
    if dataset_config_dir is None:
        return TaskDecomposition()
    existing = read_json_optional(dataset_config_dir / "task_context.json")
    if existing:
        return TaskDecomposition.from_dict(existing)
    task_description = read_text_optional(dataset_config_dir / "task_description.md")
    if not task_description:
        return TaskDecomposition()
    result = await decompose_prompt_fields(task_description)
    tc_dict = dict(result["task_context"])
    tc_dict["raw_description"] = task_description
    task_context = TaskDecomposition.from_dict(tc_dict)
    write_json(dataset_config_dir / "task_context.json", task_context.to_dict())
    return task_context
