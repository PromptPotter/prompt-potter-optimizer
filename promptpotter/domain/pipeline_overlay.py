"""The SHAPE of a ``pipeline_params`` dict — one declared answer to "is this key a node config?",
and the three readers that need it. Every consumer of the tunable surface walks it through
``node_config_items``; re-deriving ``k == "steps" and isinstance(v, dict)`` at a call site is how
two sites came to disagree about what a node config is."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "RESERVED_PIPELINE_PARAM_KEYS",
    "fold_schema_descriptions",
    "node_config_items",
    "overlay_is_locked_axis_only",
    "overlay_sets_model_outside_allowed",
]


RESERVED_PIPELINE_PARAM_KEYS: frozenset[str] = frozenset({"steps"})
"""Keys in ``pipeline_params`` that are NOT node-config dicts. ``steps`` is the
wire scaffold (the active-node list every connector's outbound payload reads);
everything else is a ``{node: {param: value}}`` config block. The single source
of truth for the "is this a node config or reserved?" question — read this or
``node_config_items`` instead of re-deriving ``k == "steps" and isinstance(...)``
at each site."""


def node_config_items(pp: dict[str, Any] | None) -> Iterator[tuple[str, dict[str, Any]]]:
    """The canonical walk over a ``pipeline_params`` dict's tunable surface — skips the reserved
    wire keys and any non-dict value."""
    for k, v in (pp or {}).items():
        if k in RESERVED_PIPELINE_PARAM_KEYS or not isinstance(v, dict):
            continue
        yield k, v


def overlay_is_locked_axis_only(overlay: dict[str, Any] | None) -> bool:
    """A pure model/provider steer leaves the origin unchanged in every other respect, so the fork
    INHERITS the done C0 instead of re-scoring it. Gates the inherit path, not the taint."""
    keys = [k for _node, cfg in node_config_items(overlay) for k in cfg]
    return bool(keys) and all(k in PARAM_FORBIDDEN_KEYS for k in keys)


def overlay_sets_model_outside_allowed(
    overlay: dict[str, Any] | None, allowed_models: list[str] | None
) -> bool:
    """A ``provider`` edit has no allow-list that could sanction it, so it always counts."""
    allowed = set(allowed_models or [])
    for _node, cfg in node_config_items(overlay):
        if "provider" in cfg:
            return True
        model = cfg.get("model")
        if model is not None and model not in allowed:
            return True
    return False


def fold_schema_descriptions(pp: dict[str, Any] | None, schema: PipelineSchema) -> None:
    """*schema* is REQUIRED — a node declaring its schema by registry identity carries none to write
    on, and without it two opposite steers produced a byte-identical payload whose hashes collided."""
    for node, cfg in node_config_items(pp):
        descriptions = cfg.pop(SCHEMA_DESCRIPTIONS_PARAM, None)
        if not isinstance(descriptions, dict) or not descriptions:
            continue
        out_schema = cfg.get("output_schema")
        if not isinstance(out_schema, dict):
            resolved = schema.get_node(node)
            out_schema = (
                copy.deepcopy(resolved.output_schema.json_schema)
                if resolved and resolved.output_schema
                else None
            )
            if not isinstance(out_schema, dict) or not out_schema:
                continue
            cfg["output_schema"] = out_schema
        props = out_schema.get("properties")
        if not isinstance(props, dict):
            continue
        for field, text in descriptions.items():
            if (
                field in props
                and isinstance(props[field], dict)
                and isinstance(text, str)
                and text.strip()
            ):
                props[field] = {**props[field], "description": text}
