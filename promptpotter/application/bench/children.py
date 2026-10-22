from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.opt_search_point import Locus
from promptpotter.domain.pipeline_schema import SCHEMA_OWNED_FIELDS, PipelineSchema
from promptpotter.domain.search_point import (
    PARAM_FORBIDDEN_KEYS,
    SHOTS_KEY,
    missing_template_vars,
)
from promptpotter.domain.wounds import ValidationFailure

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint

logger = logging.getLogger(__name__)

__all__ = [
    "DROPPED_MANDATORY_PLACEHOLDER",
    "admission_failures",
    "carried_writes",
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


def _off_carrier(locus: Locus, value: object, allowed: list[str]) -> ValidationFailure:
    return ValidationFailure(
        axis=locus.name, value=str(value)[:300], allowed=allowed, reason="unknown_param"
    )


def carried_writes(
    schema: PipelineSchema,
    *,
    prompt_loci: Mapping[Locus, Any],
    overlay: Mapping[str, Any],
    demo: Collection[str] | None,
) -> tuple[dict[Locus, Any], dict[str, dict[str, Any]], list[ValidationFailure]]:
    """Each carrier writes its own kind or nothing: a refused write is a failure on the child, never a config write."""
    failures: list[ValidationFailure] = []
    ghosts: set[str] = set()

    def declared(node: str) -> bool:
        if schema.get_node(node) is not None:
            return True
        if node not in ghosts:
            ghosts.add(node)
            # NON-FATAL: the phantom edit is stripped, the child's real edits still score.
            failures.append(
                ValidationFailure(
                    axis=node,
                    value=node,
                    allowed=sorted(schema.active_steps),
                    reason="hallucinated_node",
                )
            )
        return False

    prompts: dict[Locus, Any] = {}
    for locus, value in prompt_loci.items():
        if not declared(locus.node):
            continue
        node = schema.get_node(locus.node)
        assert node is not None
        match schema.locus_kind(*locus):
            case "prompt":
                prompts[locus] = value
            case "shots":
                if not value:
                    prompts[locus] = value
                elif demo is None:
                    failures.append(
                        ValidationFailure(
                            axis=locus.name,
                            value=", ".join(map(str, value)),
                            allowed=[],
                            reason="shots_without_demo_pool",
                        )
                    )
                elif strays := [key for key in value if key not in demo]:
                    failures.append(
                        ValidationFailure(
                            axis=locus.name,
                            value=", ".join(map(str, strays)),
                            allowed=[],
                            reason="shot_not_in_demo_pool",
                        )
                    )
                else:
                    prompts[locus] = value
            case "config":
                shots = [SHOTS_KEY] if node.prompt_info is not None else []
                failures.append(_off_carrier(locus, value, [*node.prompt_fields, *shots]))
    config: dict[str, dict[str, Any]] = {}
    for name, params in overlay.items():
        if not isinstance(params, dict) or not declared(name):
            continue
        for key, value in params.items():
            if schema.locus_kind(name, key) == "config":
                config.setdefault(name, {})[key] = value
            else:
                emittable = sorted(schema.node_param_keys().get(name, set()))
                failures.append(_off_carrier(Locus(name, key), value, emittable))
    return prompts, config, failures


def overlay_failures(
    pipeline_overlay: Mapping[str, Mapping[str, Any]],
    pipeline_schema: PipelineSchema,
    *,
    running: Mapping[str, Any],
) -> list[ValidationFailure]:
    """The deterministic twin of a wire schema, judged on the model *running* (the child's config) answers with."""
    failures: list[ValidationFailure] = []
    emittable = pipeline_schema.node_param_keys()
    for node_name, node_params in pipeline_overlay.items():
        node = pipeline_schema.get_node(node_name)
        assert node is not None, "carried_writes strips an undeclared node first"
        node_types = node.param_types
        node_emittable = emittable.get(node_name, set())
        own = running.get(node_name)
        proposed = own.get("model") if isinstance(own, dict) else None
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
            # `is not None`, never truthiness: `[]` is a declared axis.
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
    failures: list[ValidationFailure] = []
    for node in pipeline_schema.prompt_nodes():
        assert node.prompt_info is not None
        declared = node.prompt_info.template_variables
        rendered = individual.render(node)
        if missing := missing_template_vars(rendered, declared, fields=node.prompt_fields):
            failures.append(
                ValidationFailure(
                    axis=f"{node.name}.prompt",
                    value="dropped:" + ",".join(missing),
                    allowed=list(declared),
                    reason=DROPPED_MANDATORY_PLACEHOLDER,
                )
            )
    return failures


def admission_failures(
    child: OptSearchPoint,
    config: Mapping[str, Mapping[str, Any]],
    schema: PipelineSchema,
    *,
    carried: list[ValidationFailure],
) -> list[ValidationFailure]:
    """*config* is every config locus the child was written, *carried* what ``carried_writes`` refused."""
    found = [
        *carried,
        *overlay_failures(config, schema, running=child.pipeline_params),
        *placeholder_failures(child, schema),
    ]
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
