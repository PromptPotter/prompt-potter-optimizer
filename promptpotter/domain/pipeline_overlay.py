"""The SHAPE of a ``pipeline_params`` dict — one declared answer to "is this key a node config?",
and the three readers that need it. Every consumer of the tunable surface walks it through
``node_config_items``; re-deriving ``k == "steps" and isinstance(v, dict)`` at a call site is how
two sites came to disagree about what a node config is."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import SCHEMA_DESCRIPTIONS_PARAM
from promptpotter.domain.search_point import WHO_ANSWERS_KEYS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "RESERVED_PIPELINE_PARAM_KEYS",
    "fold_schema_descriptions",
    "node_config_items",
    "overlay_is_locked_axis_only",
    "overlay_sets_model_outside_allowed",
    "permitted_models_from_narrowing",
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
    """A steer touching only WHO ANSWERS leaves the origin unchanged in every other respect, so the
    fork INHERITS the done C0 instead of re-scoring it. Gates the inherit path, not the taint.

    Reads :data:`WHO_ANSWERS_KEYS`, not the forbidden subset — ``model`` is a searchable axis, so
    the narrower set misses the model-only steer, which re-asks one question of a different
    responder and is exactly the case this inherit exists for."""
    keys = [k for _node, cfg in node_config_items(overlay) for k in cfg]
    return bool(keys) and all(k in WHO_ANSWERS_KEYS for k in keys)


def permitted_models_from_narrowing(
    narrowing: Mapping[str, object] | None,
) -> dict[str, list[str]]:
    """The per-node permitted model set, read off a campaign's frozen
    ``config.optimizer_narrowing``. The ONE derivation of it, so the fork gate, the runner and the
    CLI cannot disagree about which models a branch sanctions."""
    out: dict[str, list[str]] = {}
    for node, block in (narrowing or {}).items():
        values = getattr(block, "param_allowed_values", None)
        if values is None and isinstance(block, dict):
            values = block.get("param_allowed_values")
        models = (values or {}).get("model") if isinstance(values, dict) else None
        if isinstance(models, list):
            out[node] = [str(m) for m in models]
    return out


def overlay_sets_model_outside_allowed(
    overlay: dict[str, Any] | None, permitted: Mapping[str, Sequence[str]] | None
) -> bool:
    """The ADR-0005 babysit trigger: does this steer pick a responder the origin never sanctioned?

    *permitted* is per NODE — the node's own ``param_allowed_values["model"]``. A node absent from
    it sanctions nothing, the restrictive default. A ``provider`` edit has no permitted set that
    could sanction it, so it always counts."""
    for node, cfg in node_config_items(overlay):
        if "provider" in cfg:
            return True
        model = cfg.get("model")
        if model is not None and model not in set((permitted or {}).get(node, ())):
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
