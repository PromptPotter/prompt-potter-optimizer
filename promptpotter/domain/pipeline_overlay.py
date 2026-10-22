from __future__ import annotations

import copy
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated, Any

from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    OUTPUT_CONTRACT_KEYS,
    SCHEMA_TOGGLE_PARAM,
    described_field,
    description_path,
)
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, WHO_ANSWERS_KEYS
from promptpotter.shared.hashing import shapes_optimizer_prompt

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping, Sequence

    from promptpotter.domain.pipeline_schema import (
        NodeSearchNarrowing,
        ParamSource,
        PipelineSchema,
    )

logger = logging.getLogger(__name__)

__all__ = [
    "RESERVED_PIPELINE_PARAM_KEYS",
    "apply_node_overlay",
    "fold_output_contract",
    "merge_pipeline_params",
    "node_config_items",
    "normalize_model_id",
    "overlay_is_locked_axis_only",
    "overlay_sets_model_outside_allowed",
    "permitted_models_from_narrowing",
    "pin_default_routes",
    "pinned_route",
    "steers_disallowed_model",
]


# The `pipeline_params` keys that are NOT node-config dicts: `steps` is the wire scaffold.
RESERVED_PIPELINE_PARAM_KEYS: Annotated[frozenset[str], shapes_optimizer_prompt] = frozenset(
    {"steps"}
)


@shapes_optimizer_prompt
def node_config_items(pp: dict[str, Any] | None) -> Iterator[tuple[str, dict[str, Any]]]:
    for k, v in (pp or {}).items():
        if k in RESERVED_PIPELINE_PARAM_KEYS or not isinstance(v, dict):
            continue
        yield k, v


def apply_node_overlay(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    schema: PipelineSchema | None,
    *,
    source: ParamSource | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> dict[str, Any]:
    merged = dict(base)
    for node, cfg in overlay.items():
        existing = merged.get(node)
        if not (isinstance(existing, dict) and isinstance(cfg, dict)):
            merged[node] = cfg
            _stamp(provenance, source, node, cfg)
            continue
        node_obj = schema.get_node(node) if schema else None
        param_types = node_obj.param_types if node_obj else {}
        node_cfg = {**existing, **cfg}
        # A route names ONE model's hosts: a new model without one takes its own default.
        if "route_order" not in cfg and cfg.get("model", existing.get("model")) != existing.get(
            "model"
        ):
            node_cfg.pop("route_order", None)
        for param, incoming in cfg.items():
            prior = existing.get(param)
            if (
                param_types.get(param) == "object"
                and isinstance(prior, dict)
                and isinstance(incoming, dict)
            ):
                node_cfg[param] = {**prior, **incoming}
        merged[node] = node_cfg
        _stamp(provenance, source, node, cfg)
    return merged


def merge_pipeline_params(
    base: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    schema: PipelineSchema | None,
) -> dict[str, Any] | None:
    if not overrides:
        return base
    merged = apply_node_overlay(base or {}, overrides, schema)
    if schema:
        # DECLARED, not the running chain: a node reached only by escalating exists.
        _declared = {n.name for n in schema.declared_nodes}
        for k, _cfg in list(node_config_items(merged)):
            if k not in _declared:
                logger.warning("Dropping LLM override for undeclared node %r", k)
                del merged[k]
    return merged


def _stamp(
    provenance: MutableMapping[str, dict[str, ParamSource]] | None,
    source: ParamSource | None,
    node: str,
    cfg: object,
) -> None:
    if provenance is None or source is None or not isinstance(cfg, dict):
        return
    provenance.setdefault(node, {}).update(dict.fromkeys(cfg, source))


def overlay_is_locked_axis_only(overlay: dict[str, Any] | None) -> bool:
    """Gates the C0 inherit. :data:`WHO_ANSWERS_KEYS`, not the forbidden subset, which misses a model-only steer."""
    keys = [k for _node, cfg in node_config_items(overlay) for k in cfg]
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
    overlay: dict[str, Any] | None, permitted: Mapping[str, Sequence[str]] | None
) -> bool:
    """The ADR-0005 babysit trigger. A node absent from *permitted* sanctions nothing; a cost lever always counts."""
    for node, cfg in node_config_items(overlay):
        if cfg.keys() & PARAM_FORBIDDEN_KEYS:
            return True
        model = cfg.get("model")
        if model is not None and model not in set((permitted or {}).get(node, ())):
            return True
    return False


def steers_disallowed_model(
    narrowing: Mapping[str, NodeSearchNarrowing], overlay: dict[str, Any] | None
) -> bool:
    """A surface NAMING the permitted list serves :func:`permitted_models_from_narrowing`, never its own."""
    return overlay_sets_model_outside_allowed(overlay, permitted_models_from_narrowing(narrowing))


def normalize_model_id(model: str) -> str:
    """``:nitro`` picks a HOST, and every host of one model takes the same parameters."""
    return model.split(":", 1)[0].strip().lower()


def pinned_route(
    cfg: Mapping[str, object],
    provider: str | None,
    routes: Mapping[str, Mapping[str, list[str]]],
) -> list[str] | None:
    """The host a node left to the gateway gets; an explicit ``route_order``, ``[]`` included, opts out."""
    model = cfg.get("model")
    if cfg.get("route_order") is not None or not isinstance(model, str):
        return None
    route = routes.get(str(provider), {}).get(normalize_model_id(model))
    return None if route is None else list(route)


def pin_default_routes(pp: dict[str, Any], schema: PipelineSchema) -> dict[str, Any]:
    """Before the key: a node left to the gateway's choice of host is keyed as the host it gets."""
    out = dict(pp)
    for node, cfg in node_config_items(pp):
        declared = schema.get_node(node)
        provider = cfg.get("provider") or (declared.provider if declared else None)
        if route := pinned_route(cfg, provider, schema.default_routes):
            out[node] = {**cfg, "route_order": route}
    return out


def fold_output_contract(pp: dict[str, Any] | None, schema: PipelineSchema) -> dict[str, Any]:
    """The toggle is read, never WRITTEN: an unmoved node keeps a byte-identical payload, and so its banked hash."""
    out = dict(pp or {})
    for node, cfg in node_config_items(pp):
        descriptions = {
            path: text for key, text in cfg.items() if (path := description_path(key)) is not None
        }
        folded = {key: value for key, value in cfg.items() if description_path(key) is None}
        if folded.get(SCHEMA_TOGGLE_PARAM) == ANSWER_AS_TEXT:
            # BOTH keys go: `answer_field` read off a slotless response is "" on every sample (NO_RESULT).
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
    return out
