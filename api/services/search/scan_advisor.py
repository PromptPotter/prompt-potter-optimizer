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

from api.models.pipeline_schema import PipelineSchema, PipelineStep

logger = logging.getLogger(__name__)


# Keys that map to structural LLM config (not tunable knobs)
STRUCTURAL_OVERRIDES = {"prompt", "output_schema", "model"}


def _relevant_steps(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[tuple[PipelineStep, bool]]:
    """Return (step, is_excluded) pairs for all schema steps."""
    return [
        (step, bool(excluded_steps and step.name in excluded_steps))
        for step in schema.steps
    ]


def build_pipeline_overview(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 1 — high-level pipeline structure.

    Step name, type, runtime, role, excluded flag. No config details.
    """
    overview = []
    for step, excluded in _relevant_steps(schema, excluded_steps):
        entry: dict[str, Any] = {
            "name": step.name,
            "type": step.type,
            "runtime": step.runtime,
        }
        if step.node_role:
            entry["node_role"] = step.node_role
        if step.short_circuit:
            entry["short_circuit"] = True
        if excluded:
            entry["excluded"] = True
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
    for step, _excluded in _relevant_steps(schema, excluded_steps):
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


def build_llm_context(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Layer 3 — LLM node details (output schemas, prompt templates).

    Only includes steps that have output_schema or prompt_meta.
    Presented as reference context, not primary optimization targets.
    """
    context = []
    for step, _excluded in _relevant_steps(schema, excluded_steps):
        if not step.output_schema and not step.prompt_meta:
            continue
        entry: dict[str, Any] = {"name": step.name}
        if step.output_schema:
            schema_entry: dict[str, Any] = {
                "fields": step.output_schema.fields,
            }
            if step.output_schema.field_descriptions:
                schema_entry["field_descriptions"] = step.output_schema.field_descriptions
            if step.output_schema.json_schema:
                schema_entry["json_schema"] = step.output_schema.json_schema
            entry["output_schema"] = schema_entry
        if step.prompt_meta:
            entry["prompt_meta"] = {
                "family": step.prompt_meta.family,
                "template_variables": step.prompt_meta.template_variables,
                "description": step.prompt_meta.description,
            }
            if step.prompt_meta.template:
                entry["prompt_meta"]["template"] = step.prompt_meta.template
        context.append(entry)
    return context


def _build_advisor_prompt(
    pipeline_overview: list[dict],
    tunable_params: list[dict],
    llm_context: list[dict],
    prompt_field_axes: list[str],
    pipeline_description: str,
    excluded_steps: set[str] | None = None,
    task_description: str = "",
) -> str:
    """Build the LLM prompt for scan configuration advice.

    Uses progressive pipeline disclosure: overview → tunable params → LLM details.
    """
    excluded_section = ""
    if excluded_steps:
        excluded_section = f"""
## Excluded Steps
The following steps are EXCLUDED: {sorted(excluded_steps)}.
Do NOT recommend any axes belonging to these steps — they are inactive.
"""

    task_context_section = ""
    if task_description:
        task_context_section = f"""
## Task Context
{task_description}
"""

    llm_context_section = ""
    if llm_context:
        llm_context_section = f"""
## LLM Node Details (for reference)
Output schemas and prompt templates used by LLM-driven steps:
{json.dumps(llm_context, indent=2)}
"""

    return f"""You are an expert prompt optimization advisor. Analyze this pipeline \
and recommend which axes (parameters and prompt fields) to focus a sensitivity scan on.

## Pipeline
{pipeline_description or "No description available."}

## Pipeline Steps
{json.dumps(pipeline_overview, indent=2)}
{excluded_section}{task_context_section}\
## Tunable Parameters (per step)
{json.dumps(tunable_params, indent=2)}

## Prompt Field Axes
Prompt template fields that can be varied (variant library has pre-defined values):
{json.dumps(prompt_field_axes, indent=2)}
{llm_context_section}\
## Your Task
Recommend which axes to scan in priority order. For pipeline_param axes, \
suggest concrete values to try. Use the current_values shown above as your \
reference point — suggest values that meaningfully differ from the current setting. \
For prompt_field axes, just flag them — the variant library already has values.

Return a JSON object with this structure:
{{
  "priority_axes": [
    {{
      "axis": "<param_name>",
      "source": "pipeline_param",
      "step": "<step_name>",
      "rationale": "...",
      "suggested_values": [0.0, 0.3, 0.7],
      "importance": "high"
    }},
    {{
      "axis": "<field_name>",
      "source": "prompt_field",
      "rationale": "...",
      "importance": "medium"
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
- Keep rationales to 1-2 sentences
- importance: "high", "medium", or "low"
- source: "pipeline_param" or "prompt_field"
- For pipeline_param axes: include "step" and "suggested_values"
- For prompt_field axes: omit "step" and "suggested_values"
- Do NOT recommend *_model axes. Model selection is a user decision driven by \
cost, latency, and capability tradeoffs — not an optimization target. Place them \
in axes_to_skip.
- Skip axes unlikely to affect accuracy (explain why)
- Prioritize axes with the highest expected impact
- Consider DATA FLOW between steps. Steps form a sequential pipeline where each \
step's output feeds the next. Parameters on UPSTREAM steps have multiplier effects: \
if an upstream step produces poor-quality data, downstream tuning cannot compensate. \
Pay special attention to upstream string parameters (prefixes, suffixes, query \
modifiers) that shape what data enters the pipeline — especially when their current \
values are empty or generic.
- *_schema axes are output schema overrides. Suggest MUTATIONS relative to the \
current output_schema shown in the LLM Node Details. Each suggested_value is a JSON \
array of mutation arrays applied together as one variant. Use JSON arrays, NOT tuples. \
Mutation formats:
    ["-", "field_path"]                                  remove a field
    ["+", "field_path", "type", required, "description"] add a field
    ["~", "old_path", "new_name", "type", required, "description"]  replace
  Types: "string", "array", "integer", "number", "boolean", "object".
  required: true/false. Always include type, required, description for add/replace.
  The baseline (unchanged schema) is automatically included — do NOT include it

Return ONLY the JSON object, no markdown fences or extra text."""


def preview_advisor_prompt(
    pipeline_schema: PipelineSchema | None = None,
    variant_library: dict | None = None,
    pipeline_params: dict | None = None,
    task_description: str = "",
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

        if exclude_steps is not None:
            excluded_steps = set(exclude_steps) if exclude_steps else None
        else:
            excluded_steps = _excluded_from_schema(pipeline_schema, pipeline_params)

        layer_args = dict(schema=pipeline_schema, excluded_steps=excluded_steps)
        return _build_advisor_prompt(
            pipeline_overview=build_pipeline_overview(**layer_args),
            tunable_params=build_tunable_params(**layer_args),
            llm_context=build_llm_context(**layer_args),
            prompt_field_axes=_extract_prompt_field_axes(variant_library),
            pipeline_description=pipeline_schema.description,
            excluded_steps=excluded_steps,
            task_description=task_description,
        )

    # Fallback: placeholder mode
    return _build_advisor_prompt(
        pipeline_overview=[{"<build_pipeline_overview(schema=svc['pipeline_schema'])>": "..."}],
        tunable_params=[{"<build_tunable_params(schema=svc['pipeline_schema'])>": "..."}],
        llm_context=[{"<build_llm_context(schema=svc['pipeline_schema'])>": "..."}],
        prompt_field_axes=["<load_variant_library()['prompt_fields'].keys()>"],
        pipeline_description="<svc['pipeline_schema'].description>",
        excluded_steps={"<campaign_config['exclude_steps']>"},
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
    step_param_keys = schema.step_param_keys()
    excluded_param_keys: set[str] = set()
    if excluded_steps:
        for step_name in excluded_steps:
            excluded_param_keys.update(step_param_keys.get(step_name, set()))

    # Collect all pipeline param keys
    all_param_keys: set[str] = set()
    for step in schema.steps:
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
    all_step_names = {s.name for s in schema.steps}
    active = set(pipeline_params["steps"])
    excluded = all_step_names - active
    return excluded or None


async def advise_scan_config(
    pipeline_schema: PipelineSchema,
    variant_library: dict,
    llm_client: Any,
    model: str = "",
    max_tokens: int = 2000,
    pipeline_params: dict | None = None,
    task_description: str = "",
    exclude_steps: list[str] | None = None,
) -> dict:
    """Generate LLM-powered scan configuration advice.

    Args:
        pipeline_schema: PipelineSchema describing the connected backend.
        variant_library: Variant library dict (with ``prompt_fields`` key).
        llm_client: LLM client instance (GroqClient, OpenAIClient, etc.).
        model: Model identifier for the LLM call.
        max_tokens: Maximum response tokens for the LLM call.
        pipeline_params: Pipeline params dict with ``steps`` key. Excluded
            steps are derived by diffing schema steps vs active steps.
        task_description: Domain context for the advisor LLM (e.g. input patterns,
            matching challenges). Helps generate domain-specific value suggestions.
        exclude_steps: Explicit list of steps to exclude. When provided,
            overrides the automatic schema-vs-pipeline_params diff.

    Returns:
        Advisory dict with ``priority_axes``, ``suggested_n_diagnostic``,
        ``axes_to_skip``, ``budget_breakdown``, ``reasoning``, and
        ``validation_warnings``.
    """
    if exclude_steps is not None:
        excluded_steps = set(exclude_steps) if exclude_steps else None
    else:
        excluded_steps = _excluded_from_schema(pipeline_schema, pipeline_params)

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
        excluded_steps=excluded_steps,
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
