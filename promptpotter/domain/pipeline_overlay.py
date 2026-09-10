"""The SHAPE of a ``pipeline_params`` dict — one declared answer to "is this key a node config?",
and the three readers that need it. Every consumer of the tunable surface walks it through
``node_config_items``; re-deriving ``k == "steps" and isinstance(v, dict)`` at a call site is how
two sites came to disagree about what a node config is."""

from __future__ import annotations

import copy
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from promptpotter.domain.pipeline_schema import (
    ANSWER_AS_TEXT,
    OUTPUT_CONTRACT_KEYS,
    SCHEMA_DESCRIPTIONS_PARAM,
    SCHEMA_TOGGLE_PARAM,
)
from promptpotter.domain.search_point import PARAM_FORBIDDEN_KEYS, WHO_ANSWERS_KEYS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "RESERVED_PIPELINE_PARAM_KEYS",
    "allowed_values_from_narrowing",
    "fold_output_contract",
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


def allowed_values_from_narrowing(
    narrowing: Mapping[str, object] | None,
) -> dict[str, dict[str, list[str]]]:
    """What a frozen ``config.optimizer_narrowing`` DECLARES, per node and per param. The ONE
    shape-read of it: a block is a :class:`NodeSearchNarrowing` in memory and a plain dict off
    disk, so a caller spelling that itself sees one of the two and nothing in the other."""
    out: dict[str, dict[str, list[str]]] = {}
    for node, block in (narrowing or {}).items():
        values = getattr(block, "param_allowed_values", None)
        if values is None and isinstance(block, dict):
            values = block.get("param_allowed_values")
        if isinstance(values, dict):
            out[node] = {
                param: [str(v) for v in vals]
                for param, vals in values.items()
                if isinstance(vals, list)
            }
    return out


def permitted_models_from_narrowing(
    narrowing: Mapping[str, object] | None,
) -> dict[str, list[str]]:
    """The per-node permitted model set, so the fork gate, the runner and the CLI cannot disagree
    about which models a branch sanctions."""
    return {
        node: values["model"]
        for node, values in allowed_values_from_narrowing(narrowing).items()
        if "model" in values
    }


def overlay_sets_model_outside_allowed(
    overlay: dict[str, Any] | None, permitted: Mapping[str, Sequence[str]] | None
) -> bool:
    """The ADR-0005 babysit trigger: does this steer pick a responder the origin never sanctioned?

    *permitted* is per NODE — the node's own ``param_allowed_values["model"]``. A node absent from
    it sanctions nothing, the restrictive default. A cost lever (`PARAM_FORBIDDEN_KEYS` — the
    gateway and the route) has no permitted set that could sanction it, so an edit to one always
    counts. The SET, not one member of it: naming ``provider`` alone left ``route_order`` locked in
    the browser and free on the wire."""
    for node, cfg in node_config_items(overlay):
        if cfg.keys() & PARAM_FORBIDDEN_KEYS:
            return True
        model = cfg.get("model")
        if model is not None and model not in set((permitted or {}).get(node, ())):
            return True
    return False


def fold_output_contract(pp: dict[str, Any] | None, schema: PipelineSchema) -> None:
    """Resolve the two structured-output levers onto the wire config: whether the node uses its
    schema at all, and what its `description` prose says.

    *schema* is REQUIRED — a node declaring its schema by registry identity carries none to write
    on, and without it two opposite steers produced a byte-identical payload whose hashes collided.

    The toggle is read, never WRITTEN: an unmoved node keeps a byte-identical payload, so every
    banked measurement stays addressed by the hash it was measured under, and only a candidate
    that actually chose ``text`` pays for a new one."""
    for node, cfg in node_config_items(pp):
        descriptions = cfg.pop(SCHEMA_DESCRIPTIONS_PARAM, None)
        if cfg.get(SCHEMA_TOGGLE_PARAM) == ANSWER_AS_TEXT:
            # BOTH keys go, not just the schema: a backend destructuring `answer_field` out of a
            # response that never had the slot reads "" for every sample and grades the run
            # NO_RESULT. The descriptions were popped above, so the fold below cannot resolve a
            # registry schema back onto a node that just said it wants none.
            for key in OUTPUT_CONTRACT_KEYS:
                cfg.pop(key, None)
            continue
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
