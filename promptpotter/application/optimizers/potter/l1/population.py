from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.potter.validators.l1_strict import (
    L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    L1_INNER_LAYOUT_APPLIES,
    L1_INNER_PLACEHOLDERS_INTACT,
    L1_INNER_STEER_IS_LEGAL,
    L1_PROMPT_FIELD_NOT_GUTTED,
    L1_PROMPT_FIELDS_OPEN,
)
from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_overlay import merge_pipeline_params
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import Proposal
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.runner.inner.tasks import InnerCells

logger = logging.getLogger(__name__)

__all__ = ["parse_population"]


def parse_population(
    proposals: list[Proposal],
    parent: OptSearchPoint,
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema,
    *,
    runtime_failures: Sequence[RuntimeFailure],
    inner_cells: InnerCells | None,
) -> list[Proposal]:
    """L1's rejections only: search space, route keys and backend placeholders are the bench's (``NodeContext.child``); shots are judged on the wire (``generate.py``), where demo ids still exist."""
    parsed: list[Proposal] = []
    for cp in proposals:
        pipeline_overlay = cp.overlay
        opt_sp = cp.individual
        merged_pp = merge_pipeline_params(pipeline_params, pipeline_overlay, schema)
        failures: list[ValidationFailure] = []
        if schema:
            # The DELTA, never the child's whole prompt: an inherited field was not proposed.
            prompt_edit = candidate_delta(opt_sp.prompts, parent.prompts, None, None).prompt
            held_outcome = L1_PROMPT_FIELDS_OPEN.check(prompt_edit, pipeline_schema=schema)
            if held_outcome is not None:
                failures.extend(held_outcome.evidence["failures"])
            if pipeline_overlay:
                rf_outcome = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.check(
                    pipeline_overlay,
                    runtime_failures=runtime_failures,
                    pipeline_params=merged_pp,
                )
                if rf_outcome is not None:
                    failures.extend(rf_outcome.evidence["failures"])
                # The DELTA, unlike the placeholder check below: these convict what was PROPOSED.
                for outcome in (
                    L1_INNER_STEER_IS_LEGAL.check(
                        pipeline_overlay,
                        inner_nodes=() if inner_cells is None else inner_cells.owners,
                    ),
                    L1_INNER_LAYOUT_APPLIES.check(pipeline_overlay),
                    L1_PROMPT_FIELD_NOT_GUTTED.check(
                        pipeline_overlay,
                        inner_cells=inner_cells,
                        pipeline_params=pipeline_params,
                    ),
                ):
                    if outcome is not None:
                        failures.extend(outcome.evidence["failures"])
            # The MERGED params: a child of a broken parent inherits a severed port without proposing it.
            ph_outcome = L1_INNER_PLACEHOLDERS_INTACT.check(
                merged_pp or {}, inner_cells=inner_cells
            )
            if ph_outcome is not None:
                failures.extend(ph_outcome.evidence["failures"])
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
