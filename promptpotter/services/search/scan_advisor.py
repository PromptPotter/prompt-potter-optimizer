"""Scan advisor — LLM-powered sensitivity scan configuration, plus schema mutation model.

Analyzes the connected pipeline (nodes, params, node types) and recommends
which axes to focus the sensitivity scan on, BEFORE running it.

Key design principle: no static axis enrichment. The advisor dynamically reads
``PipelineSchema`` and asks the LLM to suggest values. This makes the system
backend-agnostic — a different PipelineSchema automatically surfaces its own axes.

Schema mutation model — compact tuple-based mutations against a baseline JSON Schema.
Ops: ``-`` remove, ``+`` add, ``~`` replace (sibling swap).
Nesting: dot-separated paths (``ranked_candidates.key_match_factors``).
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
from typing import Any, Literal

from pydantic import BaseModel

from promptpotter.models.pipeline_schema import NodeOutputSchema, PipelineNode, PipelineSchema
from promptpotter.models.task_decomposition import TaskDecomposition
from promptpotter.services.optimizer.pipeline import llm_call

logger = logging.getLogger(__name__)


_TYPE_MAP: dict[str, dict] = {
    "string": {"type": "string"},
    "array": {"type": "array", "items": {"type": "string"}},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "object": {"type": "object"},
}


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


def apply_mutation(baseline: dict, variant: SchemaVariant) -> dict:
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


def build_schema_variants(
    baseline: dict,
    variants: list[SchemaVariant],
) -> list[dict]:
    """Resolve variants against baseline. Baseline always at index 0."""
    return [copy.deepcopy(baseline)] + [apply_mutation(baseline, v) for v in variants]


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


# Keys that map to structural LLM config (not tunable knobs)
STRUCTURAL_OVERRIDES = {"prompt", "output_schema", "model"}


def _active_nodes(
    schema: PipelineSchema,
    excluded_nodes: set[str] | None = None,
) -> list[PipelineNode]:
    """Return pipeline nodes that are not excluded."""
    return list(schema.exclude(excluded_nodes).nodes)


def build_pipeline_overview(
    schema: PipelineSchema,
    excluded_nodes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 1 — high-level pipeline structure.

    Node name, type, short_circuit flag. No config details.
    """
    overview = []
    for node in _active_nodes(schema, excluded_nodes):
        entry: dict[str, Any] = {"name": node.name}
        if node.node_type:
            entry["node_type"] = node.node_type
        if node.short_circuit:
            entry["short_circuit"] = True
        overview.append(entry)
    return overview


def build_tunable_params(
    schema: PipelineSchema,
    excluded_nodes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 2 — tunable knobs per node.

    Shows param_keys and current_values, stripping structural overrides
    (prompt, output_schema, model) which are not numeric/string "knobs".
    """
    tunable = []
    for node in _active_nodes(schema, excluded_nodes):
        if not node.param_keys:
            continue
        # Strip structural keys (prompt, output_schema, model)
        structural_keys = node.param_keys & STRUCTURAL_OVERRIDES

        tunable_config = (
            {k: v for k, v in node.current_config.items() if k not in structural_keys}
            if node.current_config
            else {}
        )

        entry: dict[str, Any] = {
            "name": node.name,
            "param_keys": sorted(node.param_keys),
        }
        if tunable_config:
            entry["current_values"] = tunable_config
        tunable.append(entry)
    return tunable


def _flatten_schema_fields(
    output_schema: NodeOutputSchema,
) -> dict[str, str]:
    """Flatten an output schema into a dot-path → ``"type — description"`` map.

    Walks ``json_schema["properties"]``.  For array-of-object fields, recurses
    into ``items.properties`` using ``parent.child`` dot-notation — the same
    format used by schema mutation paths.

    Falls back to ``fields`` + ``field_descriptions`` (flat) when
    ``json_schema`` is empty.
    """
    js = output_schema.json_schema
    if not js or "properties" not in js:
        # Fallback: flat fields only (no nesting info available)
        result: dict[str, str] = {}
        descs = output_schema.field_descriptions or {}
        for f in output_schema.fields:
            desc = descs.get(f, "")
            result[f] = f"string — {desc}" if desc else "string"
        return result

    def _walk(props: dict, prefix: str = "") -> dict[str, str]:
        out: dict[str, str] = {}
        for name, prop in props.items():
            path = f"{prefix}{name}"
            ptype = prop.get("type", "string")
            desc = prop.get("description", "")
            out[path] = f"{ptype} — {desc}" if desc else ptype
            # Recurse into array-of-object items
            if ptype == "array":
                items = prop.get("items", {})
                if items.get("type") == "object" and "properties" in items:
                    out.update(_walk(items["properties"], f"{path}."))
        return out

    return _walk(js.get("properties", {}))


def build_llm_context(
    schema: PipelineSchema,
    excluded_nodes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 3 — LLM node details (flattened output schema fields, prompt metadata).

    Only includes non-excluded nodes that have output_schema or prompt_meta.
    Presented as reference context, not primary optimization targets.
    """
    context = []
    for node in _active_nodes(schema, excluded_nodes):
        if not node.output_schema and not node.prompt_meta:
            continue
        entry: dict[str, Any] = {"name": node.name}
        if node.output_schema:
            entry["output_schema"] = _flatten_schema_fields(node.output_schema)
        if node.prompt_meta:
            entry["prompt_meta"] = {
                "family": node.prompt_meta.family,
                "template_variables": node.prompt_meta.template_variables,
                "description": node.prompt_meta.description,
            }
        context.append(entry)
    return context


def _advisor_system_section(pipeline_description: str) -> str:
    """Role definition and constraints."""
    return (
        "You are an expert prompt optimization advisor. Recommend which axes "
        "(parameters and prompt fields) to prioritize in a sensitivity scan.\n\n"
        "## Constraints (apply strictly)\n"
        "- Do NOT recommend *_model axes — place them in axes_to_skip.\n"
        "- Response must fit within 1500 tokens. Be terse.\n\n"
        f"## Pipeline: {pipeline_description or 'No description available.'}\n"
        "Nodes execute sequentially — each node's output feeds the next:"
    )


def _advisor_task_context_section(task_description: str | dict | TaskDecomposition) -> str:
    """Optional task context from user description or structured TaskDecomposition."""
    if isinstance(task_description, TaskDecomposition):
        tc_lines = "\n".join(f"- **{k}**: {v}" for k, v in task_description.items() if v)
        if tc_lines:
            return f"\n## Task Context\n{tc_lines}\n"
        return ""
    if isinstance(task_description, dict) and task_description:
        tc_lines = "\n".join(f"- **{k}**: {v}" for k, v in task_description.items() if v)
        if tc_lines:
            return f"\n## Task Context\n{tc_lines}\n"
    elif task_description:
        return f"\n## Task Context\n{task_description}\n"
    return ""


def _advisor_llm_context_section(llm_context: list[dict]) -> str:
    """LLM node output schema and prompt metadata."""
    if not llm_context:
        return ""
    return (
        "\n## LLM Node Details\n"
        "Output schema fields and prompt metadata for LLM-driven nodes:\n"
        f"{json.dumps(llm_context, indent=2)}\n"
    )


_ADVISOR_ANALYSIS_APPROACH = """\
## Analysis Approach
Work through these analysis steps before producing your recommendation:
1. Trace data flow: UPSTREAM parameters have multiplier effects — poor upstream \
data cannot be compensated by downstream tuning.
2. Flag high-impact targets: empty or default string params (prefixes, suffixes, \
query modifiers) that shape what data enters the pipeline.
3. For *_schema axes: identify output fields that are redundant or missing for \
downstream consumption. Suggest mutations relative to the current output_schema \
shown in LLM Node Details.
4. Skip axes unlikely to affect accuracy. Prioritize axes with the highest \
expected impact on end-to-end accuracy.
5. Estimate a diagnostic budget (queries per axis)."""


_ADVISOR_OUTPUT_FORMAT = """\
## Output Format
For pipeline_param axes, suggest concrete values that meaningfully differ from \
current_values. For prompt_field axes, just flag them — the variant library \
already has values.

Return a JSON object with this structure:
{{
  "priority_axes": [
    {{
      "axis": "<param_name>",
      "source": "pipeline_param",
      "node": "<node_name>",
      "rationale": "...",
      "suggested_values": ["<val>", "..."],
      "importance": "<high|medium|low>"
    }},
    {{
      "axis": "<field_name>",
      "source": "prompt_field",
      "rationale": "...",
      "importance": "<high|medium|low>"
    }}
  ],
  "suggested_n_diagnostic": 6,
  "axes_to_skip": [
    {{"axis": "<name>", "reason": "..."}}
  ],
  "budget_breakdown": {{
    "<axis_name>": <n_queries>,
    "total": <sum>
  }},
  "reasoning": "Overall strategy explanation..."
}}

Rules:
- rationale: max 15 words
- reasoning: max 2 sentences
- axes_to_skip reason: max 10 words
- suggested_values: 2-4 values, numbers or short strings only
- importance: "high", "medium", or "low"
- source: "pipeline_param" or "prompt_field"
- For pipeline_param axes: include "node" and "suggested_values"
- CRITICAL: For pipeline_param axes, "axis" must be an EXACT key from the \
Tunable Parameters param_keys above. Do NOT invent names or combine node \
names with param names — copy the key exactly as listed.
- For prompt_field axes: omit "node" and "suggested_values"
- *_schema mutations: each suggested_value is a JSON array of mutation arrays. \
Ops: ["-","path"] remove | ["+","path","type",required,"desc"] add | \
["~","old","new","type",required,"desc"] replace. \
Types: string|array|integer|number|boolean|object. required: true|false. \
Baseline included automatically — do NOT include it. \
Keep each variant to 1-2 mutations so individual effects are measurable.

Return ONLY the JSON object, no markdown fences or extra text."""


def _build_advisor_prompt(
    pipeline_overview: list[dict],
    tunable_params: list[dict],
    llm_context: list[dict],
    prompt_field_axes: list[str],
    pipeline_description: str,
    task_description: str | dict = "",
    search_memory_section: str = "",
) -> str:
    """Build the LLM prompt for scan configuration advice.

    Assembles from section helpers: system → pipeline → task context →
    params → prompt fields → LLM details → analysis → output format.
    """
    sections = [
        _advisor_system_section(pipeline_description),
        json.dumps(pipeline_overview, indent=2),
        _advisor_task_context_section(task_description),
        f"## Tunable Parameters (per node)\n{json.dumps(tunable_params, indent=2)}",
        (
            "## Prompt Field Axes\n"
            "Prompt template fields that can be varied (variant library has pre-defined values):\n"
            f"{json.dumps(prompt_field_axes, indent=2)}"
        ),
        _advisor_llm_context_section(llm_context),
        search_memory_section,
        _ADVISOR_ANALYSIS_APPROACH,
        _ADVISOR_OUTPUT_FORMAT,
    ]
    return "\n\n".join(s for s in sections if s)


def preview_advisor_prompt(
    pipeline_schema: PipelineSchema | None = None,
    variant_library: dict | None = None,
    pipeline_params: dict | None = None,
    task_description: str | dict = "",
    exclude_nodes: list[str] | None = None,
) -> str:
    """Return the advisor prompt — with real data when available, else placeholders.

    When *pipeline_schema* is provided, calls the actual layer builders to
    produce the exact prompt the LLM would receive.  When ``None``, falls
    back to representative placeholders so all conditional sections are visible.
    """
    if pipeline_schema is not None:
        from promptpotter.config.variant_library import load_variant_library as _load_vl

        if variant_library is None:
            variant_library = _load_vl()

        excluded_nodes = _resolve_excluded_nodes(
            exclude_nodes,
            pipeline_schema,
            pipeline_params,
        )
        return _build_advisor_prompt(
            pipeline_overview=build_pipeline_overview(
                schema=pipeline_schema, excluded_nodes=excluded_nodes
            ),
            tunable_params=build_tunable_params(
                schema=pipeline_schema, excluded_nodes=excluded_nodes
            ),
            llm_context=build_llm_context(schema=pipeline_schema, excluded_nodes=excluded_nodes),
            prompt_field_axes=_extract_prompt_field_axes(variant_library),
            pipeline_description=pipeline_schema.description,
            task_description=task_description,
        )

    # Fallback: placeholder mode
    return _build_advisor_prompt(
        pipeline_overview=[{"<build_pipeline_overview(schema=session.pipeline_schema)>": "..."}],
        tunable_params=[{"<build_tunable_params(schema=session.pipeline_schema)>": "..."}],
        llm_context=[{"<build_llm_context(schema=session.pipeline_schema)>": "..."}],
        prompt_field_axes=["<load_variant_library()['prompt_fields'].keys()>"],
        pipeline_description="<session.pipeline_schema.description>",
        task_description="<TASK_DESCRIPTION>",
    )


def _extract_prompt_field_axes(variant_library: dict) -> list[str]:
    """Extract prompt field axis names from variant library."""
    pf = variant_library.get("prompt_fields", {})
    return sorted(pf.keys())


def _build_search_memory_section(search_memory: Any) -> str:
    """Build the SearchMemory context section for the advisor prompt."""
    if search_memory is None:
        return ""

    lines = ["## Historical Intelligence (from previous evaluations)"]

    rankings = search_memory.axis_rankings()[:5]
    if rankings:
        lines.append("Parameter impact rankings (by effect size):")
        for a in rankings:
            top_vals = search_memory.top_k_values(a.axis, k=3)
            val_str = ", ".join(f"{v.value_preview} ({v.mean_accuracy:.1%})" for v in top_vals)
            lines.append(
                f"  - {a.axis}: effect={a.effect_size:.3f}, {a.classification} [{val_str}]"
            )

    bottleneck = search_memory.bottleneck_distribution()
    if bottleneck:
        lines.append("Pipeline bottleneck distribution:")
        for step, frac in bottleneck.items():
            lines.append(f"  - {step}: {frac:.0%} of failures")

    return "\n".join(lines) if len(lines) > 1 else ""


def _validate_advisory(
    advisory: dict,
    schema: PipelineSchema,
    variant_library: dict,
    excluded_nodes: set[str] | None = None,
) -> list[str]:
    """Validate LLM advisory against schema and variant library.

    Returns list of warning strings (empty = valid).
    """
    warnings: list[str] = []

    # Build node-to-params mapping for excluded-node checks
    node_param_map = schema.node_param_keys()
    excluded_param_keys: set[str] = set()
    if excluded_nodes:
        for node_name in excluded_nodes:
            excluded_param_keys.update(node_param_map.get(node_name, set()))

    # Collect all pipeline param keys
    all_param_keys: set[str] = set()
    for node in schema.nodes:
        all_param_keys.update(node.param_keys)

    prompt_fields = set(_extract_prompt_field_axes(variant_library))

    # Schema axes: names ending with _schema that are pipeline_params
    _schema_axes = {k for k in all_param_keys if k.endswith("_schema")}

    for ax in advisory.get("priority_axes", []):
        axis_name = ax.get("axis", "")
        source = ax.get("source", "")

        if source == "pipeline_param":
            if axis_name not in all_param_keys:
                warnings.append(
                    f"pipeline_param axis '{axis_name}' not found in "
                    f"PipelineSchema param_keys: {sorted(all_param_keys)}"
                )
            elif axis_name in excluded_param_keys:
                node_name = ax.get("node", "?")
                warnings.append(
                    f"pipeline_param axis '{axis_name}' belongs to excluded "
                    f"node '{node_name}' — will have no effect"
                )

            # Validate schema axis mutation tuples
            if axis_name in _schema_axes and ax.get("suggested_values"):
                for i, sv in enumerate(ax["suggested_values"]):
                    if not isinstance(sv, list):
                        continue
                    try:
                        variant = parse_mutation_tuples(sv)
                        for m in variant.mutations:
                            if m.op in ("add", "replace") and not m.description:
                                warnings.append(
                                    f"Schema axis '{axis_name}' variant {i}: "
                                    f"empty description for '{m.path}'"
                                )
                            if not isinstance(m.required, bool):
                                warnings.append(
                                    f"Schema axis '{axis_name}' variant {i}: "
                                    f"required is not bool for '{m.path}'"
                                )
                    except ValueError as e:
                        warnings.append(f"Schema axis '{axis_name}' variant {i}: parse error: {e}")

        elif source == "prompt_field":
            if axis_name not in prompt_fields:
                warnings.append(
                    f"prompt_field axis '{axis_name}' not found in "
                    f"variant_library prompt_fields: {sorted(prompt_fields)}"
                )

    budget = advisory.get("budget_breakdown", {})
    if budget.get("total", 0) < 0:
        warnings.append(f"Budget total is negative: {budget.get('total')}")

    return warnings


def _resolve_excluded_nodes(
    exclude_nodes: list[str] | None,
    schema: PipelineSchema,
    pipeline_params: dict | None,
) -> set[str] | None:
    """Resolve excluded nodes from explicit list or schema diff."""
    if exclude_nodes is not None:
        return set(exclude_nodes) if exclude_nodes else None
    if not pipeline_params or "steps" not in pipeline_params:
        return None
    excluded = schema.excluded_nodes(pipeline_params)
    return excluded or None


def resolve_schema_axes(
    raw_variants: dict,
    pipeline_schema: PipelineSchema | None,
) -> tuple[dict, dict[str, list[str]]]:
    """Resolve schema mutation tuples in *raw_variants* into concrete JSON Schema dicts.

    Non-schema axes pass through unchanged.  Returns ``(resolved, schema_labels)``.
    """
    resolved: dict = {}
    schema_labels: dict[str, list[str]] = {}

    for axis_name, vals in raw_variants.items():
        if (
            axis_name.endswith("_schema")
            and pipeline_schema is not None
            and vals
            and isinstance(vals[0], list)
        ):
            node_name = pipeline_schema.node_for_param(axis_name)
            node = pipeline_schema.get_node(node_name) if node_name else None
            if node and node.output_schema:
                try:
                    variants = [parse_mutation_tuples(sv) for sv in vals]
                    baseline = baseline_schema_from_node(node.output_schema)
                    resolved[axis_name] = build_schema_variants(baseline, variants)
                    schema_labels[axis_name] = ["(baseline)"] + [v.render_label() for v in variants]
                    continue
                except (ValueError, KeyError) as e:
                    logger.warning(
                        "Schema axis '%s' mutation parse failed, falling through to raw values: %s",
                        axis_name,
                        e,
                    )
        resolved[axis_name] = vals

    return resolved, schema_labels


def convert_advisory_to_scan_variants(
    advisory: dict,
    pipeline_schema: PipelineSchema | None = None,
) -> tuple[dict, dict[str, list[str]]]:
    """Convert scan advisory into nested scan_variants dict.

    Extracts ``priority_axes`` entries whose ``source`` is ``"pipeline_param"``
    and that carry ``suggested_values``.  Groups params under their owning
    node name: ``{"web_search": {"max_sites": [3, 5, 7]}}``.

    For schema axes (name ends with ``_schema``), parses mutation tuples,
    resolves against the baseline JSON Schema, and returns concrete dicts.

    Returns ``(scan_variants, schema_labels)`` where ``schema_labels`` maps
    schema axis names to human-readable labels (``"(baseline)"`` at index 0).
    """
    raw: dict[str, list] = {}
    node_map: dict[str, str] = {}  # axis_name → node_name
    for ax in advisory.get("priority_axes", []):
        if ax.get("source") != "pipeline_param" or not ax.get("suggested_values"):
            continue
        vals = ax["suggested_values"]
        # LLMs sometimes return mutation arrays as JSON strings — parse them
        if ax["axis"].endswith("_schema"):
            parsed = []
            for v in vals:
                if isinstance(v, str):
                    with contextlib.suppress(json.JSONDecodeError, ValueError):
                        v = json.loads(v)
                parsed.append(v)
            vals = parsed
        raw[ax["axis"]] = vals
        if ax.get("node"):
            node_map[ax["axis"]] = ax["node"]

    resolved, schema_labels = resolve_schema_axes(raw, pipeline_schema)

    # Group resolved axes under node names (nested format)
    nested: dict[str, dict] = {}
    for axis_name, vals in resolved.items():
        node = node_map.get(axis_name)
        if not node and pipeline_schema:
            node = pipeline_schema.node_for_param(axis_name)
        if node:
            nested.setdefault(node, {})[axis_name] = vals
        else:
            # Fallback: bare key (shouldn't happen with valid advisory)
            nested[axis_name] = vals

    return nested, schema_labels


async def advise_scan_config(
    pipeline_schema: PipelineSchema,
    variant_library: dict,
    llm_client: Any,
    model: str = "",
    max_tokens: int = 2000,
    pipeline_params: dict | None = None,
    task_description: str | dict = "",
    exclude_nodes: list[str] | None = None,
    search_memory: Any = None,
) -> dict:
    """Generate LLM-powered scan configuration advice.

    Args:
        pipeline_schema: PipelineSchema describing the connected backend.
        variant_library: Variant library dict (with ``prompt_fields`` key).
        llm_client: LLM client instance.
        model: Model identifier for the LLM call.
        max_tokens: Maximum response tokens for the LLM call.
        pipeline_params: Pipeline params dict with ``steps`` key. Excluded
            nodes are derived by diffing schema nodes vs active nodes.
        task_description: Domain context for the advisor LLM. Either a raw
            string or a structured task_context dict.
        exclude_nodes: Explicit list of nodes to exclude. When provided,
            overrides the automatic schema-vs-pipeline_params diff.

    Returns:
        Advisory dict with ``priority_axes``, ``suggested_n_diagnostic``,
        ``axes_to_skip``, ``budget_breakdown``, ``reasoning``, and
        ``validation_warnings``.
    """
    excluded_nodes = _resolve_excluded_nodes(
        exclude_nodes,
        pipeline_schema,
        pipeline_params,
    )
    pipeline_overview = build_pipeline_overview(
        schema=pipeline_schema, excluded_nodes=excluded_nodes
    )
    tunable_params = build_tunable_params(schema=pipeline_schema, excluded_nodes=excluded_nodes)
    llm_context = build_llm_context(schema=pipeline_schema, excluded_nodes=excluded_nodes)
    prompt_field_axes = _extract_prompt_field_axes(variant_library)

    sm_section = _build_search_memory_section(search_memory)

    prompt = _build_advisor_prompt(
        pipeline_overview=pipeline_overview,
        tunable_params=tunable_params,
        llm_context=llm_context,
        prompt_field_axes=prompt_field_axes,
        pipeline_description=pipeline_schema.description,
        task_description=task_description,
        search_memory_section=sm_section,
    )

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": prompt}],
        node="scan_advisor",
        model=model,
        max_tokens=max_tokens,
    )

    truncated = response.finish_reason in ("max_tokens", "length")
    if truncated:
        logger.warning(
            "Scan advisor response truncated (finish_reason=%s, max_tokens=%d). "
            "Increase max_tokens in optimizer_llm config.",
            response.finish_reason,
            max_tokens,
        )

    advisory = response.parsed
    if advisory is None:
        reason = (
            f"Response truncated at {max_tokens} tokens — increase max_tokens in optimizer_llm config."
            if truncated
            else "LLM response was not valid JSON"
        )
        logger.warning("Scan advisor: %s: %s", reason, response.content[:300])
        return {
            "priority_axes": [],
            "suggested_n_diagnostic": 6,
            "axes_to_skip": [],
            "budget_breakdown": {},
            "reasoning": f"{reason}. Raw: {response.content[:500]}",
            "validation_warnings": [reason],
            "raw_response": response.content,
        }

    # Validate against schema
    warnings = _validate_advisory(
        advisory,
        pipeline_schema,
        variant_library,
        excluded_nodes=excluded_nodes,
    )
    if warnings:
        for w in warnings:
            logger.warning("Scan advisor validation: %s", w)

    advisory["validation_warnings"] = warnings
    return advisory


# ---------------------------------------------------------------------------
# Scan variant flattening / rebuilding (extracted from display layer)
# ---------------------------------------------------------------------------


def flatten_scan_variants(scan_variants: dict) -> dict[str, list]:
    """Flatten nested ``{"node": {"param": [...]}}`` to flat ``{"param": [...]}``.

    List values pass through unchanged; dict values (node groups) are
    expanded so each param becomes a top-level key.
    """
    flat: dict[str, list] = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            flat[key] = spec
        elif isinstance(spec, dict):
            for param, vals in spec.items():
                if isinstance(vals, list):
                    flat[param] = vals
    return flat


def rebuild_nested_resolved(scan_variants: dict, resolved: dict) -> dict:
    """Rebuild nested structure after flat schema resolution.

    Mirrors the original ``scan_variants`` shape, replacing list values
    with their resolved counterparts from *resolved*.
    """
    nested: dict = {}
    for key, spec in scan_variants.items():
        if isinstance(spec, list):
            nested[key] = resolved.get(key, spec)
        elif isinstance(spec, dict):
            node_group = {}
            for param, vals in spec.items():
                if isinstance(vals, list):
                    node_group[param] = resolved.get(param, vals)
            nested[key] = node_group
    return nested
