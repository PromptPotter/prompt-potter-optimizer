"""L1 population shaping — between generation (``CandidateProposal``) and scoring (``ScoredCandidate``)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizers.potter.validators.l1_strict import (
    L1_CONFIG_NOT_IN_RUNTIME_FAILURES,
    L1_INNER_LAYOUT_APPLIES,
    L1_INNER_STEER_IS_LEGAL,
    L1_PROMPT_BLOCKS_IN_LIBRARY,
    L1_PROMPT_FIELD_NOT_GUTTED,
    L1_PROMPT_FIELDS_OPEN,
    L1_PROMPT_PLACEHOLDERS_INTACT,
    L1_SCHEMA_COMPLIANCE,
    L1_SHOTS_IN_DEMO_POOL,
)
from promptpotter.application.pipeline_resolve import merge_pipeline_params
from promptpotter.domain.candidate_diff import candidate_delta
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.results import CandidateProposal
from promptpotter.domain.wounds import RuntimeFailure, ValidationFailure

if TYPE_CHECKING:
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.optimizers.potter.knobs import PromptBlockCatalogue

logger = logging.getLogger(__name__)

__all__ = ["parse_population"]


def parse_population(
    proposals: list[CandidateProposal],
    parent: OptSearchPoint,
    pipeline_params: dict[str, Any] | None,
    schema: PipelineSchema | None,
    *,
    runtime_failures: Sequence[RuntimeFailure],
    demo_ids: frozenset[int],
    shot_k_max: int,
    inner_optimizer: SelectedOptimizer | None,
    prompt_block_catalogue: PromptBlockCatalogue,
) -> tuple[list[OptSearchPoint], list[dict[str, Any] | None]]:
    """Project proposals into searchpoints. ``provider`` / ``route_order`` mutations are ALWAYS
    rejected — cost levers, never on L1's surface; ``model`` rides only where its node opened it,
    bounded by that node's permitted set. An off-library prompt-field value is rejected only under
    ``restrict``."""
    opt_sp_list: list[OptSearchPoint] = []
    merged: list[dict[str, Any] | None] = []
    parent_fields = parent.prompt_fields()
    for cp in proposals:
        pipeline_overlay = cp.pipeline_overlay
        opt_sp = cp.opt_sp
        merged_pp = merge_pipeline_params(pipeline_params, pipeline_overlay, schema)
        failures: list[ValidationFailure] = []
        if opt_sp.shot_ids != parent.shot_ids:
            shots_outcome = L1_SHOTS_IN_DEMO_POOL.check(
                {"shot_ids": opt_sp.shot_ids}, demo_ids=demo_ids, k_max=shot_k_max
            )
            if shots_outcome is not None:
                failures.extend(shots_outcome.evidence["failures"])
        if schema:
            # The DELTA, never the child's whole prompt: an inherited field was not proposed.
            prompt_edit = candidate_delta(opt_sp.prompt_fields(), parent_fields, None, None).prompt
            block_outcome = L1_PROMPT_BLOCKS_IN_LIBRARY.check(
                prompt_edit,
                prompt_block_catalogue=prompt_block_catalogue,
            )
            if block_outcome is not None:
                failures.extend(block_outcome.evidence["failures"])
            held_outcome = L1_PROMPT_FIELDS_OPEN.check(prompt_edit, pipeline_schema=schema)
            if held_outcome is not None:
                failures.extend(held_outcome.evidence["failures"])
            if pipeline_overlay:
                outcome = L1_SCHEMA_COMPLIANCE.check(
                    pipeline_overlay,
                    pipeline_schema=schema,
                )
                if outcome is not None:
                    failures.extend(outcome.evidence["failures"])
                # Re-propose check: rejects (param, value) already in the cycle's runtime
                # wounds; runs even when schema-compliance passes.
                rf_outcome = L1_CONFIG_NOT_IN_RUNTIME_FAILURES.check(
                    pipeline_overlay,
                    runtime_failures=runtime_failures,
                    pipeline_params=merged_pp,
                )
                if rf_outcome is not None:
                    failures.extend(rf_outcome.evidence["failures"])
                # The DELTA, like the two above and unlike the placeholder check below: these
                # convict a candidate for what it PROPOSED, and a child inheriting an ancestor's
                # prose proposed nothing. The gutting check takes the parent's params because the
                # length it judges is a COMPARISON — the delta alone cannot say what it replaced.
                for outcome in (
                    L1_INNER_STEER_IS_LEGAL.check(
                        pipeline_overlay, inner_optimizer=inner_optimizer
                    ),
                    L1_INNER_LAYOUT_APPLIES.check(pipeline_overlay),
                    L1_PROMPT_FIELD_NOT_GUTTED.check(
                        pipeline_overlay,
                        inner_optimizer=inner_optimizer,
                        pipeline_params=pipeline_params,
                    ),
                ):
                    if outcome is not None:
                        failures.extend(outcome.evidence["failures"])
            # Mandatory placeholders intact — the evolved TARGET prompt (runs even when the
            # mutation is prompt-fields-only, the exact case that drops {{combined_text}})
            # AND, on an L4 campaign, the MERGED inner optimizer prompts (a child of a broken
            # parent inherits a severed port without re-proposing it).
            ph_outcome = L1_PROMPT_PLACEHOLDERS_INTACT.check(
                merged_pp or {},
                inner_optimizer=inner_optimizer,
                opt_sp=opt_sp,
                pipeline_schema=schema,
            )
            if ph_outcome is not None:
                failures.extend(ph_outcome.evidence["failures"])
        if failures:
            cp.validation_failures = failures
            for vf in failures:
                logger.warning(
                    "candidate %s: validation failure on %s — proposed %r not in allowed %r (reason=%s)",
                    opt_sp.lineage.id[:8],
                    vf.axis,
                    vf.value,
                    vf.allowed,
                    vf.reason,
                )
        opt_sp_list.append(opt_sp)
        merged.append(merged_pp)
    return opt_sp_list, merged
