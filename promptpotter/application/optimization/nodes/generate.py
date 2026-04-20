"""L1 candidate generation — produce pipeline-param variants via LLM meta-prompt."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.nodes.formatting import format_context_sections
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.domain.analysis import ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm.client import LLMClientBase
from promptpotter.infrastructure.tracing.events import CandidateCreated
from promptpotter.shared.constants import (
    PROMPT_STRING_FIELDS,
    TASK_CONTEXT_OVERRIDES,
    classify_axis,
)
from promptpotter.shared.errors import graceful
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.domain.analysis import FailureAnalysis
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = ["build_l1_output_schema", "l1_generate", "validate_overrides"]


def build_l1_output_schema(pipeline_schema: PipelineSchema) -> dict:
    """Construct the JSON schema L1 must conform to.

    The schema declares the top-level ``variants`` array and, for each
    active pipeline node, the shape of its ``pipeline_params_override``
    sub-object. Any param with declared ``param_allowed_values`` becomes a
    JSON-schema ``enum`` — the LLM is physically unable to emit an out-of-
    range value (e.g. ``reasoning_effort="minimal"``).

    Prompt string fields and task-context keys are declared as free strings.
    Unknown node-level params stay unconstrained; only enum axes are
    tightened. Non-strict mode so optional fields may be omitted by L1.
    """
    node_properties: dict[str, dict] = {}
    for node in pipeline_schema.nodes:
        if not node.param_keys:
            continue
        param_props: dict[str, dict] = {}
        for param in sorted(node.param_keys):
            allowed = node.param_allowed_values.get(param)
            if allowed:
                param_props[param] = {"type": "string", "enum": list(allowed)}
            else:
                param_props[param] = {}  # any JSON value permitted
        node_properties[node.name] = {
            "type": "object",
            "properties": param_props,
            "additionalProperties": False,
        }

    override_properties: dict[str, dict] = {
        **{field: {"type": "string"} for field in PROMPT_STRING_FIELDS},
        **{field: {"type": "string"} for field in TASK_CONTEXT_OVERRIDES},
        **node_properties,
    }

    variant_item = {
        "type": "object",
        "properties": {
            "variant_name": {"type": "string"},
            "changes_description": {"type": "string"},
            "pipeline_params_override": {
                "type": "object",
                "properties": override_properties,
                "additionalProperties": False,
            },
        },
        "required": ["variant_name", "changes_description"],
        "additionalProperties": False,
    }

    return {
        "name": "l1_variants",
        "strict": False,
        "schema": {
            "type": "object",
            "properties": {
                "variants": {"type": "array", "items": variant_item},
            },
            "required": ["variants"],
            "additionalProperties": False,
        },
    }


def validate_overrides(
    node_overrides: dict[str, dict],
    pipeline_schema: PipelineSchema,
) -> list[ValidationFailure]:
    """Walk node-scoped pipeline_params overrides against user-declared allowed sets.

    Two sources of truth:
      * ``model`` axis → ``PipelineSchema.available_models``
      * any param declared in ``PipelineNode.param_allowed_values`` (e.g.
        ``reasoning_effort``, ``response_format``) → that list

    Failures attach to the candidate's OptSearchPoint memory, triggering
    the synthetic-0 early exit in ``score_search_point()``. See
    ``docs/architecture/optimization.md``.
    """
    failures: list[ValidationFailure] = []
    allowed_models = list(pipeline_schema.available_models)
    allowed_models_set = set(allowed_models)
    for node_name, node_params in node_overrides.items():
        if not isinstance(node_params, dict):
            continue
        if allowed_models_set:
            proposed_model = node_params.get("model")
            if proposed_model is not None and proposed_model not in allowed_models_set:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.model",
                        value=str(proposed_model),
                        allowed=allowed_models,
                        reason="not_in_available_models",
                    )
                )
        node = pipeline_schema.get_node(node_name)
        if node is None or not node.param_allowed_values:
            continue
        for param, allowed in node.param_allowed_values.items():
            if param == "model":
                continue  # handled above against available_models
            if param not in node_params:
                continue
            proposed = node_params[param]
            if proposed not in allowed:
                failures.append(
                    ValidationFailure(
                        axis=f"{node_name}.{param}",
                        value=str(proposed),
                        allowed=list(allowed),
                        reason="not_in_param_allowed_values",
                    )
                )
    return failures


def _render_schema_text(pipeline_schema: PipelineSchema) -> str:
    """Build pipeline schema description for L1 LLM context."""
    lines: list[str] = []
    npk = pipeline_schema.node_param_keys()
    if npk:
        lines.append(
            "VALID PIPELINE NODES AND PARAMETERS (only use these — do not invent nodes or params):"
        )
        for node_name, params in npk.items():
            node = pipeline_schema.get_node(node_name)
            descs = node.param_descriptions if node else {}
            enums = node.param_allowed_values if node else {}
            if params:
                param_parts = []
                for p in sorted(params):
                    desc = descs.get(p)
                    allowed = enums.get(p)
                    suffix_bits: list[str] = []
                    if desc:
                        suffix_bits.append(desc)
                    if allowed:
                        suffix_bits.append("one of: " + ", ".join(allowed))
                    param_parts.append(f"{p} ({'; '.join(suffix_bits)})" if suffix_bits else p)
                lines.append(f"  {node_name}: {', '.join(param_parts)}")
            else:
                lines.append(f"  {node_name}: (no tunable params)")

        for node in pipeline_schema.nodes:
            if node.output_schema and node.output_schema.fields:
                os = node.output_schema
                lines.append(f"\n  CURRENT OUTPUT SCHEMA for {node.name}:")
                lines.append(f"    Fields: {', '.join(os.fields)}")
                for fname, fdesc in os.field_descriptions.items():
                    lines.append(f"      {fname}: {fdesc}")
                lines.append("    MUTATION SYNTAX (use as output_schema param):")
                lines.append('      Add:     ["+", "field_name", "array", true, "description"]')
                lines.append('      Remove:  ["-", "field_name"]')
                lines.append(
                    '      Replace: ["~", "old_name", "new_name", "array", true, "description"]'
                )
                lines.append(
                    f'    Example: {{"{node.name}": {{"output_schema": '
                    f'[["+", "domain_terms", "array", true, '
                    f'"Domain-specific database entry names"]]}}}}'
                )

        if pipeline_schema.get_node("token_matching"):
            lines.append("\n  HOW TOKEN MATCHING USES ENTITY PROFILES:")
            lines.append("    ALL entity profile field values are tokenized ([a-zA-Z0-9]+)")
            lines.append("    and matched against database entry tokens.")
            lines.append("    Score = shared_tokens / term_tokens.")
            lines.append("    Adding fields that produce tokens matching database entries")
            lines.append(
                "    DIRECTLY improves retrieval. Removing noisy fields reduces false matches."
            )

    text = "\n".join(lines)
    if pipeline_schema.available_models:
        text += "\n\nAVAILABLE MODELS (only use these for model overrides):\n"
        text += "\n".join(f"  {m}" for m in pipeline_schema.available_models)
    return text


async def l1_generate(
    opt_sp: OptSearchPoint,
    current_accuracy: float,
    current_results: list[dict],
    n_variants: int,
    creativity: float,
    llm_client: LLMClientBase,
    model: str | None = None,
    is_probe_round: bool = False,
    failure_analysis: FailureAnalysis | None = None,
    search_memory_digest: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    obs: ObservabilityBridge | None = None,
    obs_campaign_id: str = "",
    round_num: int = 0,
) -> list[dict]:
    """Generate candidate pipeline-param variants via LLM meta-prompt.

    All optimizer context (critique, thinking_styles, task_context, plan,
    escalation_journal, warning_inventory, l2_directive) is read from the
    ``OptSearchPoint`` — no need to pass them individually.

    Returns:
        List of candidate dicts (prompt field dumps with
        ``__pipeline_params_override__``).
    """
    if n_variants <= 0:
        raise ValueError(f"n_variants must be >0, got {n_variants}")

    schema_text = _render_schema_text(pipeline_schema) if pipeline_schema else ""

    _compile_vars = {
        "n_variants": str(n_variants),
        "accuracy_pct": f"{current_accuracy:.1%}",
        "n_queries": str(len(current_results)),
        "rendered_prompt": opt_sp.render(),
        "context_sections": format_context_sections(
            task_context=opt_sp.task_context or None,
            critique_text=opt_sp.memory.critique_text,
            l2_directive=opt_sp.memory.l2_directive,
            thinking_styles=opt_sp.memory.thinking_styles or None,
            plan=opt_sp.plan or "",
            warning_inventory=opt_sp.memory.warning_inventory or None,
            escalation_journal=opt_sp.memory.escalation_journal or None,
            is_probe_round=is_probe_round,
            failure_analysis=failure_analysis,
            search_memory_digest=search_memory_digest,
            pipeline_schema_text=schema_text,
        ),
    }
    _template = load_optimizer_prompt("meta_scan_aware")
    meta_prompt = _template.compile_prompt(**_compile_vars)

    output_schema = build_l1_output_schema(pipeline_schema) if pipeline_schema else None

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": meta_prompt}],
        node="l1_generate",
        model=model,
        temperature=creativity,
        json_schema=output_schema,
        trace_meta={
            "template_name": "meta_scan_aware",
            "template_fields": _template.prompt_field_dict(),
            "variables": _compile_vars,
        },
    )
    generated = extract_parsed_json(response)

    variants_list = generated.get("variants", []) if isinstance(generated, dict) else generated

    candidates: list[dict] = []
    for v in variants_list[:n_variants]:
        pp_override = v.get("pipeline_params_override") or {}
        pp_override.pop("steps", None)  # LLM must not override pipeline composition
        prompt_changes: dict = {}
        tc_changes: dict = {}
        node_overrides: dict = {}
        for k, pv in pp_override.items():
            kind = classify_axis(k)
            if kind == "prompt_field":
                prompt_changes[k] = pv
            elif kind == "task_context":
                tc_changes[k] = pv
            else:
                node_overrides[k] = pv

        # Safety net: auto-nest flat params the LLM may still emit
        if pipeline_schema:
            _flat_keys = [k for k, v in node_overrides.items() if not isinstance(v, dict)]
            for fk in _flat_keys:
                owner = pipeline_schema.node_for_param(fk)
                if owner:
                    logger.warning("l1_generate: auto-nesting flat param %r → %s", fk, owner)
                    node_overrides.setdefault(owner, {})[fk] = node_overrides.pop(fk)

            # Filter out hallucinated nodes that don't exist in the pipeline
            _bad_nodes = [k for k in node_overrides if not pipeline_schema.has_node(k)]
            for bk in _bad_nodes:
                logger.warning("l1_generate: dropping hallucinated node %r", bk)
                del node_overrides[bk]

        # Validation of node_overrides against allowed-value sets is deferred
        # to _parse_candidates in score.py — same wire dict, one producer of
        # truth, no __validation_failures__ round-trip. See
        # docs/architecture/optimization.md.
        child = opt_sp.derive_candidate(
            changes_description=v.get("changes_description", ""),
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = child.task_context.merge(tc_changes)
        c_dict = child.prompt_field_dict()
        c_dict["id"] = child.id
        c_dict["parent_id"] = child.parent_id
        c_dict["changes_description"] = child.changes_description
        if node_overrides:
            c_dict["__pipeline_params_override__"] = node_overrides
        candidates.append(c_dict)

        if obs:
            with graceful("CandidateCreated emit failed"):
                obs.emit_write_point(
                    CandidateCreated,
                    campaign_id=obs_campaign_id,
                    round_num=round_num,
                    candidate_idx=len(candidates) - 1,
                    candidate_id=child.id,
                )

    return candidates
