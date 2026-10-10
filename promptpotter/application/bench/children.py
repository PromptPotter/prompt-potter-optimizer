from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from promptpotter.application.pipeline_resolve import missing_template_vars
from promptpotter.domain.pipeline_schema import SCHEMA_OWNED_FIELDS, PipelineSchema
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint

logger = logging.getLogger(__name__)

__all__ = [
    "DROPPED_MANDATORY_PLACEHOLDER",
    "admission_failures",
    "overlay_failures",
    "placeholder_failures",
]

DROPPED_MANDATORY_PLACEHOLDER = "dropped_mandatory_placeholder"


_JSON_TYPE_TO_PY: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _matches_declared_type(value: Any, declared: str) -> bool:
    py_types = _JSON_TYPE_TO_PY.get(declared)
    if py_types is None:
        return True
    # JSON Schema booleans are NOT integers, though `isinstance(True, int)`.
    if isinstance(value, bool):
        return declared == "boolean"
    return isinstance(value, py_types)


def overlay_failures(
    pipeline_overlay: dict[str, dict[str, Any]],
    pipeline_schema: PipelineSchema,
) -> list[ValidationFailure]:
    """The deterministic twin of a wire schema: not every provider enforces one, and a node that asks no model has none."""
    failures: list[ValidationFailure] = []
    emittable = pipeline_schema.node_param_keys()
    for node_name, node_params in pipeline_overlay.items():
        if not isinstance(node_params, dict):
            continue
        node = pipeline_schema.get_node(node_name)
        if node is None:
            # NON-FATAL: ``merge_pipeline_params`` already drops the node, so the child's real edits still score.
            failures.append(
                ValidationFailure(
                    axis=node_name,
                    value=node_name,
                    allowed=sorted(pipeline_schema.active_steps),
                    reason="hallucinated_node",
                )
            )
            continue
        node_types = node.param_types
        node_emittable = emittable.get(node_name, set())
        for param, value in node_params.items():
            if param in SCHEMA_OWNED_FIELDS or param in PARAM_FORBIDDEN_KEYS:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=[],
                        reason="forbidden_axis",
                    )
                )
                continue
            # After the forbidden axes, so a leaked `provider` keeps its specific reason.
            if param not in node_emittable:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=sorted(node_emittable),
                        reason="unknown_param",
                    )
                )
                continue
            declared_type = node_types.get(param)
            if (
                declared_type
                and value is not None
                and not _matches_declared_type(value, declared_type)
            ):
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=f"{value!r} ({type(value).__name__})",
                        allowed=[declared_type],
                        reason="type_mismatch",
                    )
                )
                continue
            # Judged against the model THIS child would run on; `is not None`, never truthiness: `[]` is a declared axis.
            proposed = node_params.get("model")
            allowed = pipeline_schema.param_options(
                node, param, model=proposed if isinstance(proposed, str) else None
            )
            if allowed is not None and value is not None and value not in allowed:
                declared = node.param_allowed_values.get(param) or ()
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(value),
                        allowed=list(allowed),
                        reason=(
                            "not_in_available_models"
                            if param == "model"
                            else "not_accepted_by_model"
                            if value in declared
                            else "not_in_param_allowed_values"
                        ),
                    )
                )
    return failures


def placeholder_failures(
    individual: OptSearchPoint, pipeline_schema: PipelineSchema
) -> list[ValidationFailure]:
    """A variation may DEGRADE what flows through a channel (measurable), never delete the channel (unmeasurable)."""
    prompt_nodes = pipeline_schema.prompt_node_names()
    if not prompt_nodes:
        return []
    node = pipeline_schema.get_node(prompt_nodes[0])
    if node is None or node.prompt_info is None:
        return []
    declared = node.prompt_info.template_variables
    missing = missing_template_vars(individual.render(), declared) if declared else []
    if not missing:
        return []
    return [
        ValidationFailure(
            axis=f"{prompt_nodes[0]}.prompt",
            value="dropped:" + ",".join(missing),
            allowed=list(declared),
            reason=DROPPED_MANDATORY_PLACEHOLDER,
        )
    ]


def admission_failures(
    child: OptSearchPoint, overlay: dict[str, dict[str, Any]], schema: PipelineSchema
) -> list[ValidationFailure]:
    found = overlay_failures(overlay, schema) if overlay else []
    found += placeholder_failures(child, schema)
    for failure in found:
        logger.warning(
            "candidate %s: validation failure on %s — proposed %r not in allowed %r (reason=%s)",
            child.id[:8],
            failure.axis,
            failure.value,
            failure.allowed,
            failure.reason,
        )
    return found
