from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_params import NodeParams, PipelineParams
from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    OUTPUT_CONTRACT_KEYS,
    SCHEMA_TOGGLE_PARAM,
    described_field,
    description_path,
)
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, WHO_ANSWERS_KEYS

if TYPE_CHECKING:
    from collections.abc import MutableMapping, Sequence

    from promptpotter.domain.pipeline_schema import (
        NodeSearchNarrowing,
        ParamSource,
        PipelineSchema,
    )

logger = logging.getLogger(__name__)


def apply_node_overlay(
    base: PipelineParams,
    overlay: Mapping[str, Mapping[str, Any]],
    schema: PipelineSchema | None,
    *,
    source: ParamSource | None = None,
    provenance: MutableMapping[str, dict[str, ParamSource]] | None = None,
) -> PipelineParams:
    """The one merge of a node layer onto the carrier: each node's keys laid over its own, an
    ``object`` param merged one level, and a new model without a route taking its own default."""
    nodes = dict(base.nodes)
    for node, cfg in overlay.items():
        existing = nodes.get(node)
        if existing is None:
            nodes[node] = NodeParams.parse(cfg)
            _stamp(provenance, source, node, cfg)
            continue
        node_obj = schema.get_node(node) if schema else None
        param_types = node_obj.param_types if node_obj else {}
        laid = {**existing.dump(), **cfg}
        # A route names ONE model's hosts: a new model without one takes its own default.
        if "route_order" not in cfg and cfg.get("model", existing.model) != existing.model:
            laid.pop("route_order", None)
        for param, incoming in cfg.items():
            prior = existing.value(param)
            if (
                param_types.get(param) == "object"
                and isinstance(prior, dict)
                and isinstance(incoming, Mapping)
            ):
                laid[param] = {**prior, **incoming}
        nodes[node] = NodeParams.parse(laid)
        _stamp(provenance, source, node, cfg)
    return base.with_nodes(nodes)


def merge_pipeline_params(
    base: PipelineParams,
    overrides: Mapping[str, Mapping[str, Any]] | None,
    schema: PipelineSchema | None,
) -> PipelineParams:
    if not overrides:
        return base
    merged = apply_node_overlay(base, overrides, schema)
    if not schema:
        return merged
    # DECLARED, not the running chain: a node reached only by escalating exists.
    declared = {n.name for n in schema.declared_nodes}
    for k in merged.nodes:
        if k not in declared:
            logger.warning("Dropping LLM override for undeclared node %r", k)
    return merged.with_nodes({k: c for k, c in merged.nodes.items() if k in declared})


def _stamp(
    provenance: MutableMapping[str, dict[str, ParamSource]] | None,
    source: ParamSource | None,
    node: str,
    cfg: Mapping[str, Any],
) -> None:
    if provenance is None or source is None:
        return
    provenance.setdefault(node, {}).update(dict.fromkeys(cfg, source))


def overlay_is_locked_axis_only(overlay: Mapping[str, Mapping[str, Any]] | None) -> bool:
    """Gates the C0 inherit. :data:`WHO_ANSWERS_KEYS`, not the forbidden subset, which misses a model-only steer."""
    keys = [k for cfg in (overlay or {}).values() for k in cfg]
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
    overlay: Mapping[str, Mapping[str, Any]] | None, permitted: Mapping[str, Sequence[str]] | None
) -> bool:
    """The babysit trigger (decisions.md § 0005). A node absent from *permitted* sanctions nothing; a cost lever always counts."""
    for node, cfg in (overlay or {}).items():
        if cfg.keys() & PARAM_FORBIDDEN_KEYS:
            return True
        model = cfg.get("model")
        if model is not None and model not in set((permitted or {}).get(node, ())):
            return True
    return False


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
    nodes = dict(pp.nodes)
    for node, cfg in pp.nodes.items():
        declared = schema.get_node(node)
        provider = cfg.provider or (declared.provider if declared else None)
        if route := pinned_route(cfg.model, cfg.route_order, provider, schema.default_routes):
            nodes[node] = cfg.laid({"route_order": route})
    return pp.with_nodes(nodes)


def fold_output_contract(pp: PipelineParams, schema: PipelineSchema) -> PipelineParams:
    """The toggle is read, never WRITTEN: an unmoved node keeps a byte-identical payload, and so its banked hash."""
    nodes = dict(pp.nodes)
    for node, cfg in pp.nodes.items():
        laid = cfg.dump()
        descriptions = {
            path: text for key, text in laid.items() if (path := description_path(key)) is not None
        }
        folded = {key: value for key, value in laid.items() if description_path(key) is None}
        if folded.get(SCHEMA_TOGGLE_PARAM) == ANSWER_AS_TEXT:
            # BOTH keys go: `answer_fields` read off a slotless response is "" on every sample (NO_RESULT).
            folded = {k: v for k, v in folded.items() if k not in OUTPUT_CONTRACT_KEYS}
        elif descriptions:
            declared = folded.get("output_schema")
            if not isinstance(declared, dict):
                resolved = schema.get_node(node)
                declared = (
                    resolved.output_schema.json_schema
                    if resolved and resolved.output_schema
                    else None
                )
            if isinstance(declared, dict) and declared:
                # A copy: the declared schema is the node's or the registry's.
                out_schema = copy.deepcopy(declared)
                folded["output_schema"] = out_schema
                for path, text in descriptions.items():
                    field = described_field(out_schema, path)
                    if field is not None and isinstance(text, str) and text.strip():
                        field["description"] = text
        nodes[node] = NodeParams.parse(folded)
    return pp.with_nodes(nodes)
