from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.potter.validators.l1_strict import (
    l1_config_not_in_runtime_failures,
    l1_inner_layout_applies,
    l1_inner_placeholders_intact,
    l1_inner_steer_is_legal,
    l1_prompt_field_not_gutted,
    l1_prompt_fields_open,
    l1_prose_within_ceiling,
)
from promptpotter.domain.arms import Proposal
from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_overlay import merge_pipeline_params
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.wounds import RuntimeFailure

if TYPE_CHECKING:
    from promptpotter.application.runner.inner.tasks import InnerCells

logger = logging.getLogger(__name__)


def parse_population(
    proposals: list[Proposal],
    parent: OptSearchPoint,
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema,
    *,
    runtime_failures: Sequence[RuntimeFailure],
    inner_cells: InnerCells | None,
) -> list[Proposal]:
    """L1's rejections only: search space, route keys and backend placeholders are the bench's (``Children.child``); shots are judged on the wire (``generate.py``), where demo ids still exist."""
    parsed: list[Proposal] = []
    for cp in proposals:
        pipeline_overlay = cp.overlay
        opt_sp = cp.individual
        merged_pp = merge_pipeline_params(pipeline_params, pipeline_overlay, schema)
        # The DELTA, never the child's whole prompt: an inherited field was not proposed.
        prompt_edit = candidate_delta(opt_sp.prompts, parent.prompts, None, None).prompt
        failures = l1_prompt_fields_open(prompt_edit, pipeline_schema=schema)
        if pipeline_overlay:
            # The DELTA, unlike the placeholder check below: these convict what was PROPOSED.
            failures += [
                *l1_config_not_in_runtime_failures(
                    pipeline_overlay,
                    runtime_failures=runtime_failures,
                    pipeline_params=merged_pp,
                ),
                *l1_inner_steer_is_legal(
                    pipeline_overlay,
                    inner_nodes=() if inner_cells is None else inner_cells.owners,
                ),
                *l1_inner_layout_applies(pipeline_overlay),
                *l1_prose_within_ceiling(pipeline_overlay, schema=schema, inner_cells=inner_cells),
                *l1_prompt_field_not_gutted(
                    pipeline_overlay,
                    inner_cells=inner_cells,
                    pipeline_params=pipeline_params,
                ),
            ]
        # The MERGED params: a child of a broken parent inherits a severed port without proposing it.
        failures += l1_inner_placeholders_intact(merged_pp or {}, inner_cells=inner_cells)
        # A replayed proposal already carries them: the bench banks the walk's arms validated.
        failures = [vf for vf in failures if vf not in cp.validation_failures]
        if failures:
            cp = cp.model_copy(update={"validation_failures": [*cp.validation_failures, *failures]})
            for vf in failures:
                logger.warning(
                    "candidate %s: validation failure on %s — proposed %r not in allowed %r (reason=%s)",
                    opt_sp.id[:8],
                    vf.axis,
                    vf.value,
                    vf.allowed,
                    vf.reason,
                )
        parsed.append(cp)
    return parsed
