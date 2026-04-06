"""Schema mutation model — compact tuple-based mutations against a baseline JSON Schema.

Ops: ``-`` remove, ``+`` add, ``~`` replace (sibling swap).
Nesting: dot-separated paths (``ranked_candidates.key_match_factors``).
"""

from __future__ import annotations

import copy
import logging
from typing import Literal

from pydantic import BaseModel

from promptpotter.models.pipeline_schema import NodeOutputSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type shortcut → JSON Schema property
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, dict] = {
    "string": {"type": "string"},
    "array": {"type": "array", "items": {"type": "string"}},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "object": {"type": "object"},
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SchemaMutation(BaseModel):
    """One atomic mutation against a baseline JSON Schema."""

    model_config = {"frozen": True}

    op: Literal["add", "remove", "replace"]
    path: str
    new_field: str | None = None
    field_type: str = "array"
    required: bool = True
    description: str = ""

    def json_schema_property(self) -> dict:
        """Convert ``field_type`` + ``description`` to a JSON Schema property dict."""
        prop = dict(_TYPE_MAP.get(self.field_type, {"type": self.field_type}))
        if self.description:
            prop["description"] = self.description
        return prop


class SchemaVariant(BaseModel):
    """A list of mutations applied together as one variant."""

    model_config = {"frozen": True}

    mutations: list[SchemaMutation]

    def render_label(self) -> str:
        """Copy-pastable tuple representation of this variant."""
        parts: list[str] = []
        for m in self.mutations:
            if m.op == "remove":
                parts.append(f"('-', '{m.path}')")
            elif m.op == "add":
                parts.append(
                    f"('+', '{m.path}', '{m.field_type}', {m.required}, '{m.description}')"
                )
            elif m.op == "replace":
                parts.append(
                    f"('~', '{m.path}', '{m.new_field}', "
                    f"'{m.field_type}', {m.required}, '{m.description}')"
                )
        return ", ".join(parts)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_mutation_tuples(raw: list[tuple]) -> SchemaVariant:
    """Parse a list of raw tuples into a ``SchemaVariant``.

    Raises ``ValueError`` on malformed tuples.
    """
    mutations: list[SchemaMutation] = []
    for t in raw:
        if not isinstance(t, (tuple, list)) or len(t) < 2:
            raise ValueError(f"Mutation tuple must have >= 2 elements: {t!r}")
        op_char = t[0]
        if op_char == "-":
            if len(t) != 2:
                raise ValueError(f"Remove tuple must be ('-', path): {t!r}")
            mutations.append(SchemaMutation(op="remove", path=t[1]))
        elif op_char == "+":
            if len(t) != 5:
                raise ValueError(f"Add tuple must be ('+', path, type, required, desc): {t!r}")
            mutations.append(
                SchemaMutation(
                    op="add",
                    path=t[1],
                    field_type=t[2],
                    required=t[3],
                    description=t[4],
                )
            )
        elif op_char == "~":
            if len(t) != 6:
                raise ValueError(
                    f"Replace tuple must be ('~', old_path, new_name, type, required, desc): {t!r}"
                )
            mutations.append(
                SchemaMutation(
                    op="replace",
                    path=t[1],
                    new_field=t[2],
                    field_type=t[3],
                    required=t[4],
                    description=t[5],
                )
            )
        else:
            raise ValueError(f"Unknown mutation op '{op_char}': {t!r}")
    return SchemaVariant(mutations=mutations)


# ---------------------------------------------------------------------------
# Path walker
# ---------------------------------------------------------------------------


def _walk_path(schema: dict, path: str) -> tuple[dict, list, str]:
    """Navigate dot-separated path through nested ``properties``.

    Returns ``(parent_properties_dict, parent_required_list, leaf_field_name)``.
    """
    parts = path.split(".")
    current = schema
    for part in parts[:-1]:
        props = current.get("properties", {})
        if part not in props:
            raise KeyError(f"Intermediate path segment '{part}' not found in schema")
        current = props[part]
    props = current.get("properties", {})
    req = current.get("required", [])
    return props, req, parts[-1]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_mutation(baseline: dict, variant: SchemaVariant) -> dict:
    """Apply a ``SchemaVariant`` to a baseline JSON Schema (deep copy)."""
    schema = copy.deepcopy(baseline)
    for m in variant.mutations:
        try:
            props, req, leaf = _walk_path(schema, m.path)
        except KeyError:
            if m.op == "remove":
                logger.warning("Remove target '%s' not found in schema; skipping", m.path)
                continue
            raise

        if m.op == "remove":
            if leaf in props:
                del props[leaf]
            else:
                logger.warning("Remove target '%s' not found in properties; skipping", m.path)
            if leaf in req:
                req.remove(leaf)

        elif m.op == "add":
            props[leaf] = m.json_schema_property()
            if m.required and leaf not in req:
                req.append(leaf)

        elif m.op == "replace":
            # Remove old
            if leaf in props:
                del props[leaf]
            if leaf in req:
                req.remove(leaf)
            # Add new as sibling
            new_name = m.new_field or leaf
            props[new_name] = m.json_schema_property()
            if m.required and new_name not in req:
                req.append(new_name)

    return schema


def resolve_schema_variants(
    baseline: dict,
    variants: list[SchemaVariant],
) -> list[dict]:
    """Resolve variants against baseline. Baseline always at index 0."""
    return [copy.deepcopy(baseline)] + [resolve_mutation(baseline, v) for v in variants]


# ---------------------------------------------------------------------------
# Baseline extraction
# ---------------------------------------------------------------------------


def baseline_schema_from_node(output_schema: NodeOutputSchema) -> dict:
    """Extract the baseline JSON Schema dict from a ``NodeOutputSchema``.

    Prefers ``json_schema`` if non-empty; falls back to building from
    ``fields`` + ``field_descriptions``.
    """
    if output_schema.json_schema:
        return output_schema.json_schema
    # Fallback: build minimal schema from fields
    properties: dict[str, dict] = {}
    for field in output_schema.fields:
        prop: dict = {"type": "string"}
        desc = output_schema.field_descriptions.get(field, "")
        if desc:
            prop["description"] = desc
        properties[field] = prop
    return {
        "type": "object",
        "properties": properties,
        "required": list(output_schema.fields),
    }
