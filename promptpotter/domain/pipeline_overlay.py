from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_params import WHO_ANSWERS_KEYS
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    OUTPUT_CONTRACT_KEYS,
    SCHEMA_TOGGLE_PARAM,
    described_field,
    description_path,
    pickable,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.pipeline_params import NodeCall, PipelineParams
    from promptpotter.domain.pipeline_schema import (
        NodeSearchNarrowing,
        PipelineSchema,
    )


def overlay_is_locked_axis_only(overlay: Mapping[str, NodeCall]) -> bool:
    """Gates the C0 inherit. :data:`WHO_ANSWERS_KEYS`, not the forbidden subset, which misses a model-only steer."""
    keys = [k for call in overlay.values() for k in call.laid_keys()]
    return bool(keys) and all(k in WHO_ANSWERS_KEYS for k in keys)


def permitted_models_from_narrowing(
    narrowing: Mapping[str, NodeSearchNarrowing],
) -> dict[str, list[str]]:
    return {
        node: list(block.param_allowed_values["model"])
        for node, block in narrowing.items()
        if "model" in block.param_allowed_values
    }


def overlay_sets_model_outside_allowed(
    overlay: Mapping[str, Mapping[str, Any]] | None, permitted: Mapping[str, Sequence[str]]
) -> bool:
    """The babysit trigger: *overlay* moves a key past what a bound steer may pick, a model past
    its node's *permitted*; a ``null`` clears, and moves nothing past either."""
    return any(
        (pick := pickable(key, steer_bound=True)) == "none"
        or (pick == "permitted" and value not in permitted.get(node, ()))
        for node, cfg in (overlay or {}).items()
        for key, value in cfg.items()
        if value is not None
    )


def steers_disallowed_model(
    narrowing: Mapping[str, NodeSearchNarrowing], overlay: Mapping[str, Mapping[str, Any]] | None
) -> bool:
    """A surface NAMING the permitted list serves :func:`permitted_models_from_narrowing`, never its own."""
    return overlay_sets_model_outside_allowed(overlay, permitted_models_from_narrowing(narrowing))


def normalize_model_id(model: str) -> str:
    """``:nitro`` picks a HOST, and every host of one model takes the same parameters."""
    return model.split(":", 1)[0].strip().lower()


def pinned_route(
    model: object,
    route_order: object,
    provider: str | None,
    routes: Mapping[str, Mapping[str, list[str]]],
) -> list[str] | None:
    """The host a node left to the gateway gets; an explicit ``route_order``, ``[]`` included, opts out."""
    if route_order is not None or not isinstance(model, str):
        return None
    route = routes.get(str(provider), {}).get(normalize_model_id(model))
    return None if route is None else list(route)


def pin_default_routes(pp: PipelineParams, schema: PipelineSchema) -> PipelineParams:
    """Before the key: a node left to the gateway's choice of host is keyed as the host it gets."""
    out = dict(pp.nodes)
    for node, cfg in pp.nodes.items():
        declared = schema.get_node(node)
        provider = cfg.provider or (declared.provider if declared else None)
        if route := pinned_route(cfg.model, cfg.route_order, provider, schema.default_routes):
            out[node] = cfg.model_copy(update={"route_order": route})
    return pp.with_nodes(out)


def fold_output_contract(pp: PipelineParams, schema: PipelineSchema) -> PipelineParams:
    """The toggle is read, never WRITTEN: an unmoved node keeps a byte-identical payload, and so its banked hash."""
    out = pp.laid_nodes
    for node, cfg in pp.laid_nodes.items():
        descriptions = {
            path: text for key, text in cfg.items() if (path := description_path(key)) is not None
        }
        folded = {key: value for key, value in cfg.items() if description_path(key) is None}
        if folded.get(SCHEMA_TOGGLE_PARAM) == ANSWER_AS_TEXT:
            # BOTH keys go: `answer_fields` read off a slotless response is "" on every sample (NO_RESULT).
            out[node] = {k: v for k, v in folded.items() if k not in OUTPUT_CONTRACT_KEYS}
            continue
        out[node] = folded
        if not descriptions:
            continue
        declared = folded.get("output_schema")
        if not isinstance(declared, dict):
            resolved = schema.get_node(node)
            declared = (
                resolved.output_schema.json_schema if resolved and resolved.output_schema else None
            )
            if not isinstance(declared, dict) or not declared:
                continue
        # A copy: the declared schema is the node's or the registry's.
        out_schema = copy.deepcopy(declared)
        folded["output_schema"] = out_schema
        for path, text in descriptions.items():
            field = described_field(out_schema, path)
            if field is not None and isinstance(text, str) and text.strip():
                field["description"] = text
    return pp.with_nodes(out)
