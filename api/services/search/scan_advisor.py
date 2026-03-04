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


def _build_pipeline_anatomy(schema: PipelineSchema) -> list[dict[str, Any]]:
    """Extract structured pipeline anatomy from PipelineSchema.steps."""
    anatomy = []
    for step in schema.steps:
        entry: dict[str, Any] = {
            "name": step.name,
            "type": step.type,
            "runtime": step.runtime,
        }
        if step.param_keys:
            entry["param_keys"] = sorted(step.param_keys)
        if step.short_circuit:
            entry["short_circuit"] = True
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
) -> str:
    """Build the LLM prompt for scan configuration advice."""
    return f"""You are an expert prompt optimization advisor. Analyze this LLM pipeline \
and recommend which axes (parameters and prompt fields) to focus a sensitivity scan on.

## Pipeline Description
{pipeline_description or "No description available."}

## Pipeline Anatomy (steps, types, tunable parameters)
{json.dumps(pipeline_anatomy, indent=2)}

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
suggest concrete values to try (the parameter name and step type should guide you). \
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

Return ONLY the JSON object, no markdown fences or extra text."""


def _extract_prompt_field_axes(variant_library: dict) -> list[str]:
    """Extract prompt field axis names from variant library."""
    pf = variant_library.get("prompt_fields", {})
    return sorted(pf.keys())


def _validate_advisory(
    advisory: dict,
    schema: PipelineSchema,
    variant_library: dict,
) -> list[str]:
    """Validate LLM advisory against schema and variant library.

    Returns list of warning strings (empty = valid).
    """
    warnings: list[str] = []

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

    pipeline_anatomy = _build_pipeline_anatomy(pipeline_schema)
    prompt_field_axes = _extract_prompt_field_axes(variant_library)

    prompt = _build_advisor_prompt(
        pipeline_anatomy=pipeline_anatomy,
        prompt_field_axes=prompt_field_axes,
        baseline_accuracy=baseline_accuracy,
        eval_data_size=eval_data_size,
        query_budget=query_budget,
        pipeline_description=pipeline_schema.description,
        coverage_stats=coverage_stats,
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
    warnings = _validate_advisory(advisory, pipeline_schema, variant_library)
    if warnings:
        for w in warnings:
            logger.warning("Scan advisor validation: %s", w)

    advisory["validation_warnings"] = warnings
    return advisory
