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

from api.models.pipeline_schema import PipelineSchema

logger = logging.getLogger(__name__)


def _build_pipeline_anatomy(
    schema: PipelineSchema,
    excluded_steps: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract structured pipeline anatomy from PipelineSchema.steps.

    Includes output schema fields and prompt metadata when available,
    giving the advisor LLM visibility into what each step produces.
    Steps in ``excluded_steps`` are annotated with ``"excluded": True``.
    """
    anatomy = []
    for step in schema.steps:
        entry: dict[str, Any] = {
            "name": step.name,
            "type": step.type,
            "runtime": step.runtime,
        }
        if excluded_steps and step.name in excluded_steps:
            entry["excluded"] = True
        if step.param_keys:
            entry["param_keys"] = sorted(step.param_keys)
        if step.short_circuit:
            entry["short_circuit"] = True
        if step.output_schema:
            schema_entry: dict[str, Any] = {
                "fields": step.output_schema.fields,
            }
            if step.output_schema.field_descriptions:
                schema_entry["field_descriptions"] = step.output_schema.field_descriptions
            entry["output_schema"] = schema_entry
        if step.prompt_meta:
            entry["prompt_meta"] = {
                "family": step.prompt_meta.family,
                "template_variables": step.prompt_meta.template_variables,
                "description": step.prompt_meta.description,
            }
        if step.current_config:
            entry["current_values"] = step.current_config
        anatomy.append(entry)
    return anatomy


def _format_coverage_section(coverage_stats: dict | None) -> str:
    """Format candidate coverage stats for the advisor LLM prompt."""
    if not coverage_stats or not coverage_stats.get("total"):
        return ""
    covered = coverage_stats["covered"]
    total = coverage_stats["total"]
    pct = coverage_stats["coverage_pct"]
    return f"""
## Candidate Coverage (from baseline eval)
- Ground truth found in candidates: {covered}/{total} ({pct:.1f}%)
- If coverage is low, reranking prompt changes have limited value —
  focus on earlier pipeline steps (entity profiling, token matching).
"""


def _build_advisor_prompt(
    pipeline_anatomy: list[dict],
    prompt_field_axes: list[str],
    baseline_accuracy: float,
    eval_data_size: int,
    query_budget: int,
    pipeline_description: str,
    coverage_stats: dict | None = None,
    excluded_steps: set[str] | None = None,
    task_description: str = "",
    available_models: list[str] | None = None,
) -> str:
    """Build the LLM prompt for scan configuration advice."""
    excluded_section = ""
    if excluded_steps:
        excluded_section = f"""
## Excluded Steps
The following steps are EXCLUDED from the current evaluation configuration: \
{sorted(excluded_steps)}.
Do NOT recommend any axes (parameters or prompt fields) belonging to these steps — \
they are inactive and will have no effect on accuracy.
"""

    task_context_section = ""
    if task_description:
        task_context_section = f"""
## Task Context
{task_description}
"""

    available_models_section = ""
    if available_models:
        available_models_section = f"""
## Available Models
The backend supports the following models for LLM steps: {json.dumps(available_models)}
When recommending "profiling_model" or "ranking_model" axes, you MUST only suggest \
models from this list. If no models are listed, do NOT recommend model axes.
"""

    return f"""You are an expert prompt optimization advisor. Analyze this LLM pipeline \
and recommend which axes (parameters and prompt fields) to focus a sensitivity scan on.

## Pipeline Description
{pipeline_description or "No description available."}
{excluded_section}{task_context_section}{available_models_section}\
## Pipeline Anatomy (steps, types, tunable parameters)
{json.dumps(pipeline_anatomy, indent=2)}

## Step Output Schemas
Some steps produce structured JSON outputs that feed downstream steps.
The ``output_schema`` in the Pipeline Anatomy shows what each step produces.
Consider which output fields are most impactful for downstream accuracy —
e.g. if entity profiling produces fields that token matching uses for scoring,
changes to profiling quality directly affect candidate retrieval.

## Available Prompt Field Axes
These are the prompt template fields that can be varied during optimization:
{json.dumps(prompt_field_axes, indent=2)}

## Current Performance
- Baseline accuracy: {baseline_accuracy:.1%}
- Evaluation dataset size: {eval_data_size} queries
{_format_coverage_section(coverage_stats)}
## Budget Constraint
Total backend evaluation calls available: {query_budget}
Each axis needs N diagnostic queries × M values to scan. Plan the budget carefully.
A small diagnostic set (e.g. 6 queries) per axis value is typical.

## Your Task
Recommend which axes to scan and in what priority order. For pipeline_param axes, \
suggest concrete values to try. Use the current_values shown in the anatomy as your \
reference point — suggest values that meaningfully differ from the current setting. \
For prompt_field axes, just flag them — the variant library already has values.

Return a JSON object with this exact structure:
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
    "baseline": 6,
    "axes": 114,
    "total": 120
  }},
  "reasoning": "Overall strategy explanation..."
}}

Rules:
- importance must be "high", "medium", or "low"
- source must be "pipeline_param" or "prompt_field"
- For pipeline_param axes, always include "step" and "suggested_values"
- For prompt_field axes, omit "step" and "suggested_values"
- Budget total must not exceed {query_budget}
- Skip axes that are unlikely to affect accuracy (explain why)
- Prioritize axes with the highest expected impact
- Never recommend "ranking_prompt" or "profiling_prompt" as pipeline_param scan axes — \
these are prompt template overrides managed via prompt_field optimization, not numeric knobs
- "profiling_schema" and "ranking_schema" are output schema overrides (JSON schema dicts) that \
define the structured output each LLM step produces. These are HIGH-LEVERAGE axes: the schema \
fields directly determine what information downstream steps receive. Suggest concrete field \
additions or removals based on the current output_schema shown in the anatomy. For example, \
adding a classification field or removing a low-value field can reshape pipeline behavior
- "profiling_model" and "ranking_model" override the LLM model per-step. Model switching \
is LOW priority — the default model is well-tuned for this pipeline. Only recommend model \
axes when the budget is large enough AND other higher-impact axes are already covered. \
If you do suggest model axes, ONLY use models from the Available Models list. \
Always include the current/default model as the first value. If no Available Models \
section is present, skip model axes entirely

Return ONLY the JSON object, no markdown fences or extra text."""


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


async def advise_scan_config(
    pipeline_schema: PipelineSchema,
    variant_library: dict,
    baseline_results: list[dict],
    eval_data_size: int,
    llm_client: Any,
    model: str = "",
    query_budget: int = 120,
    coverage_stats: dict | None = None,
    excluded_steps: set[str] | None = None,
    task_description: str = "",
) -> dict:
    """Generate LLM-powered scan configuration advice.

    Args:
        pipeline_schema: PipelineSchema describing the connected backend.
        variant_library: Variant library dict (with ``prompt_fields`` key).
        baseline_results: List of baseline eval result dicts (with ``hit`` key).
        eval_data_size: Total number of queries in eval dataset.
        llm_client: LLM client instance (GroqClient, OpenAIClient, etc.).
        model: Model identifier for the LLM call.
        query_budget: Maximum number of backend eval calls to budget.
        coverage_stats: Optional dict from ``analyze_candidate_coverage()``
            with keys ``covered``, ``total``, ``coverage_pct``, ``viable``.
        excluded_steps: Step names excluded from evaluation (e.g. ``{"llm_ranking"}``).
        task_description: Domain context for the advisor LLM (e.g. input patterns,
            matching challenges). Helps generate domain-specific value suggestions.

    Returns:
        Advisory dict with ``priority_axes``, ``suggested_n_diagnostic``,
        ``axes_to_skip``, ``budget_breakdown``, ``reasoning``, and
        ``validation_warnings``.
    """
    # Compute baseline accuracy
    if baseline_results:
        hits = sum(1 for r in baseline_results if r.get("hit"))
        baseline_accuracy = hits / len(baseline_results)
    else:
        baseline_accuracy = 0.0

    pipeline_anatomy = _build_pipeline_anatomy(pipeline_schema, excluded_steps=excluded_steps)
    prompt_field_axes = _extract_prompt_field_axes(variant_library)

    prompt = _build_advisor_prompt(
        pipeline_anatomy=pipeline_anatomy,
        prompt_field_axes=prompt_field_axes,
        baseline_accuracy=baseline_accuracy,
        eval_data_size=eval_data_size,
        query_budget=query_budget,
        pipeline_description=pipeline_schema.description,
        coverage_stats=coverage_stats,
        excluded_steps=excluded_steps,
        task_description=task_description,
        available_models=pipeline_schema.available_models or None,
    )

    response = await llm_client.chat(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0.3,
        max_tokens=2000,
        output_format="json",
    )

    advisory = response.parsed
    if advisory is None:
        logger.warning("Scan advisor LLM response was not valid JSON: %s", response.content[:300])
        return {
            "priority_axes": [],
            "suggested_n_diagnostic": 6,
            "axes_to_skip": [],
            "budget_breakdown": {},
            "reasoning": f"LLM response parsing failed. Raw: {response.content[:500]}",
            "validation_warnings": ["LLM response was not valid JSON"],
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
