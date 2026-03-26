"""Scan advisor — LLM-powered sensitivity scan configuration.

Analyzes the connected pipeline (steps, params, node types) and recommends
which axes to focus the sensitivity scan on, BEFORE running it.

Key design principle: no static axis enrichment. The advisor dynamically reads
``PipelineSchema`` and asks the LLM to suggest values. This makes the system
backend-agnostic — a different PipelineSchema automatically surfaces its own axes.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from api.models.pipeline_schema import PipelineSchema, PipelineNode, StepOutputSchema

logger = logging.getLogger(__name__)


# Keys that map to structural LLM config (not tunable knobs)
STRUCTURAL_OVERRIDES = {"prompt", "output_schema", "model"}


def _active_steps(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[PipelineNode]:
    """Return pipeline steps that are not excluded."""
    if not excluded_steps:
        return list(schema.nodes)
    return [s for s in schema.nodes if s.name not in excluded_steps]


def build_pipeline_overview(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 1 — high-level pipeline structure.

    Step name, role, short_circuit flag. No config details.
    """
    overview = []
    for step in _active_steps(schema, excluded_steps):
        entry: dict[str, Any] = {"name": step.name}
        if step.node_role:
            entry["node_role"] = step.node_role
        if step.short_circuit:
            entry["short_circuit"] = True
        overview.append(entry)
    return overview


def build_tunable_params(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 2 — tunable knobs per step.

    Shows param_keys and current_values, stripping structural overrides
    (prompt, output_schema, model) which are not numeric/string "knobs".
    """
    tunable = []
    for step in _active_steps(schema, excluded_steps):
        if not step.param_keys:
            continue
        # Identify structural keys via override_map
        structural_keys: set[str] = set()
        if step.override_map:
            for flat_key, wire_key in step.override_map.items():
                if wire_key in STRUCTURAL_OVERRIDES:
                    structural_keys.add(flat_key)

        tunable_config = {
            k: v for k, v in step.current_config.items()
            if k not in structural_keys
        } if step.current_config else {}

        entry: dict[str, Any] = {
            "name": step.name,
            "param_keys": sorted(step.param_keys),
        }
        if tunable_config:
            entry["current_values"] = tunable_config
        tunable.append(entry)
    return tunable


def _flatten_schema_fields(
    output_schema: StepOutputSchema,
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
    excluded_steps: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 3 — LLM node details (flattened output schema fields, prompt metadata).

    Only includes non-excluded steps that have output_schema or prompt_meta.
    Presented as reference context, not primary optimization targets.
    """
    context = []
    for step in _active_steps(schema, excluded_steps):
        if not step.output_schema and not step.prompt_meta:
            continue
        entry: dict[str, Any] = {"name": step.name}
        if step.output_schema:
            entry["output_schema"] = _flatten_schema_fields(step.output_schema)
        if step.prompt_meta:
            entry["prompt_meta"] = {
                "family": step.prompt_meta.family,
                "template_variables": step.prompt_meta.template_variables,
                "description": step.prompt_meta.description,
            }
        context.append(entry)
    return context


def _build_advisor_prompt(
    pipeline_overview: list[dict],
    tunable_params: list[dict],
    llm_context: list[dict],
    prompt_field_axes: list[str],
    pipeline_description: str,
    task_description: str | dict = "",
) -> str:
    """Build the LLM prompt for scan configuration advice.

    Uses progressive pipeline disclosure: overview → tunable params → LLM details.

    Args:
        task_description: Raw string or structured task_context dict.
    """
    # --- conditional sections ---
    constraints_section = (
        "## Constraints (apply strictly)\n"
        "- Do NOT recommend *_model axes — place them in axes_to_skip.\n"
        "- Response must fit within 1500 tokens. Be terse."
    )

    task_context_section = ""
    if isinstance(task_description, dict) and task_description:
        tc_lines = "\n".join(f"- **{k}**: {v}" for k, v in task_description.items() if v)
        if tc_lines:
            task_context_section = f"\n## Task Context\n{tc_lines}\n"
    elif task_description:
        task_context_section = f"""
## Task Context
{task_description}
"""

    llm_context_section = ""
    if llm_context:
        llm_context_section = f"""
## LLM Node Details
Output schema fields and prompt metadata for LLM-driven steps:
{json.dumps(llm_context, indent=2)}
"""

    return f"""\
You are an expert prompt optimization advisor. Recommend which axes \
(parameters and prompt fields) to prioritize in a sensitivity scan.

{constraints_section}

## Pipeline: {pipeline_description or "No description available."}
Steps execute sequentially — each step's output feeds the next:
{json.dumps(pipeline_overview, indent=2)}
{task_context_section}\
## Tunable Parameters (per step)
{json.dumps(tunable_params, indent=2)}

## Prompt Field Axes
Prompt template fields that can be varied (variant library has pre-defined values):
{json.dumps(prompt_field_axes, indent=2)}
{llm_context_section}\
## Analysis Approach
Work through these steps before producing your recommendation:
1. Trace data flow: UPSTREAM parameters have multiplier effects — poor upstream \
data cannot be compensated by downstream tuning.
2. Flag high-impact targets: empty or default string params (prefixes, suffixes, \
query modifiers) that shape what data enters the pipeline.
3. For *_schema axes: identify output fields that are redundant or missing for \
downstream consumption. Suggest mutations relative to the current output_schema \
shown in LLM Node Details.
4. Skip axes unlikely to affect accuracy. Prioritize axes with the highest \
expected impact on end-to-end accuracy.
5. Estimate a diagnostic budget (queries per axis).

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
      "step": "<step_name>",
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
- For pipeline_param axes: include "step" and "suggested_values"
- CRITICAL: For pipeline_param axes, "axis" must be an EXACT key from the \
Tunable Parameters param_keys above. Do NOT invent names or combine step \
names with param names — copy the key exactly as listed.
- For prompt_field axes: omit "step" and "suggested_values"
- *_schema mutations: each suggested_value is a JSON array of mutation arrays. \
Ops: ["-","path"] remove | ["+","path","type",required,"desc"] add | \
["~","old","new","type",required,"desc"] replace. \
Types: string|array|integer|number|boolean|object. required: true|false. \
Baseline included automatically — do NOT include it. \
Keep each variant to 1-2 mutations so individual effects are measurable.

Return ONLY the JSON object, no markdown fences or extra text."""


def preview_advisor_prompt(
    pipeline_schema: PipelineSchema | None = None,
    variant_library: dict | None = None,
    pipeline_params: dict | None = None,
    task_description: str | dict = "",
    exclude_steps: list[str] | None = None,
) -> str:
    """Return the advisor prompt — with real data when available, else placeholders.

    When *pipeline_schema* is provided, calls the actual layer builders to
    produce the exact prompt the LLM would receive.  When ``None``, falls
    back to representative placeholders so all conditional sections are visible.
    """
    if pipeline_schema is not None:
        from api.config.settings import load_variant_library as _load_vl

        if variant_library is None:
            variant_library = _load_vl()

        excluded_steps = _resolve_excluded_steps(
            exclude_steps, pipeline_schema, pipeline_params,
        )
        layer_args = dict(schema=pipeline_schema, excluded_steps=excluded_steps)
        return _build_advisor_prompt(
            pipeline_overview=build_pipeline_overview(**layer_args),
            tunable_params=build_tunable_params(**layer_args),
            llm_context=build_llm_context(**layer_args),
            prompt_field_axes=_extract_prompt_field_axes(variant_library),
            pipeline_description=pipeline_schema.description,
            task_description=task_description,
        )

    # Fallback: placeholder mode
    return _build_advisor_prompt(
        pipeline_overview=[{"<build_pipeline_overview(schema=svc['pipeline_schema'])>": "..."}],
        tunable_params=[{"<build_tunable_params(schema=svc['pipeline_schema'])>": "..."}],
        llm_context=[{"<build_llm_context(schema=svc['pipeline_schema'])>": "..."}],
        prompt_field_axes=["<load_variant_library()['prompt_fields'].keys()>"],
        pipeline_description="<svc['pipeline_schema'].description>",
        task_description="<TASK_DESCRIPTION>",
    )


def _extract_prompt_field_axes(variant_library: dict) -> list[str]:
    """Extract prompt field axis names from variant library."""
    pf = variant_library.get("prompt_fields", {})
    return sorted(pf.keys())


def _validate_advisory(
    advisory: dict,
    schema: PipelineSchema,
    variant_library: dict,
    excluded_steps: set[str] | None = None,
) -> list[str]:
    """Validate LLM advisory against schema and variant library.

    Returns list of warning strings (empty = valid).
    """
    from api.models.schema_mutation import parse_mutation_tuples

    warnings: list[str] = []

    # Build step-to-params mapping for excluded-step checks
    step_param_keys = schema.node_param_keys()
    excluded_param_keys: set[str] = set()
    if excluded_steps:
        for step_name in excluded_steps:
            excluded_param_keys.update(step_param_keys.get(step_name, set()))

    # Collect all pipeline param keys
    all_param_keys: set[str] = set()
    for step in schema.nodes:
        all_param_keys.update(step.param_keys)

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
                step_name = ax.get("step", "?")
                warnings.append(
                    f"pipeline_param axis '{axis_name}' belongs to excluded "
                    f"step '{step_name}' — will have no effect"
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
                        warnings.append(
                            f"Schema axis '{axis_name}' variant {i}: parse error: {e}"
                        )

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


def _excluded_from_schema(
    schema: PipelineSchema, pipeline_params: dict | None,
) -> set[str] | None:
    """Derive excluded steps by diffing schema steps vs pipeline_params["steps"]."""
    if not pipeline_params or "steps" not in pipeline_params:
        return None
    all_step_names = {s.name for s in schema.nodes}
    active = set(pipeline_params["steps"])
    excluded = all_step_names - active
    return excluded or None


def _resolve_excluded_steps(
    exclude_steps: list[str] | None,
    schema: PipelineSchema,
    pipeline_params: dict | None,
) -> set[str] | None:
    """Resolve excluded steps from explicit list or schema diff."""
    if exclude_steps is not None:
        return set(exclude_steps) if exclude_steps else None
    return _excluded_from_schema(schema, pipeline_params)


async def advise_scan_config(
    pipeline_schema: PipelineSchema,
    variant_library: dict,
    llm_client: Any,
    model: str = "",
    max_tokens: int = 2000,
    pipeline_params: dict | None = None,
    task_description: str | dict = "",
    exclude_steps: list[str] | None = None,
) -> dict:
    """Generate LLM-powered scan configuration advice.

    Args:
        pipeline_schema: PipelineSchema describing the connected backend.
        variant_library: Variant library dict (with ``prompt_fields`` key).
        llm_client: LLM client instance.
        model: Model identifier for the LLM call.
        max_tokens: Maximum response tokens for the LLM call.
        pipeline_params: Pipeline params dict with ``steps`` key. Excluded
            steps are derived by diffing schema steps vs active steps.
        task_description: Domain context for the advisor LLM. Either a raw
            string or a structured task_context dict.
        exclude_steps: Explicit list of steps to exclude. When provided,
            overrides the automatic schema-vs-pipeline_params diff.

    Returns:
        Advisory dict with ``priority_axes``, ``suggested_n_diagnostic``,
        ``axes_to_skip``, ``budget_breakdown``, ``reasoning``, and
        ``validation_warnings``.
    """
    excluded_steps = _resolve_excluded_steps(
        exclude_steps, pipeline_schema, pipeline_params,
    )
    layer_args = dict(schema=pipeline_schema, excluded_steps=excluded_steps)
    pipeline_overview = build_pipeline_overview(**layer_args)
    tunable_params = build_tunable_params(**layer_args)
    llm_context = build_llm_context(**layer_args)
    prompt_field_axes = _extract_prompt_field_axes(variant_library)

    prompt = _build_advisor_prompt(
        pipeline_overview=pipeline_overview,
        tunable_params=tunable_params,
        llm_context=llm_context,
        prompt_field_axes=prompt_field_axes,
        pipeline_description=pipeline_schema.description,
        task_description=task_description,
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.3,
        max_tokens=max_tokens,
        output_format="json",
    )

    truncated = response.finish_reason in ("max_tokens", "length")
    if truncated:
        logger.warning(
            "Scan advisor response truncated (finish_reason=%s, max_tokens=%d). "
            "Increase max_tokens in eval_llm config.",
            response.finish_reason, max_tokens,
        )

    advisory = response.parsed
    if advisory is None:
        reason = (
            f"Response truncated at {max_tokens} tokens — "
            "increase max_tokens in eval_llm config."
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
        advisory, pipeline_schema, variant_library, excluded_steps=excluded_steps,
    )
    if warnings:
        for w in warnings:
            logger.warning("Scan advisor validation: %s", w)

    advisory["validation_warnings"] = warnings
    return advisory
