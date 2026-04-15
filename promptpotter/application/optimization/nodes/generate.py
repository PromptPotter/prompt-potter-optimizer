"""L1 candidate generation — produce pipeline-param variants via LLM meta-prompt."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.nodes.formatting import (
    L1PromptData,
    format_context_sections,
)
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.domain.analysis import ValidationFailure
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm.client import LLMClientBase
from promptpotter.infrastructure.tracing.events import CandidateCreated
from promptpotter.shared.constants import classify_axis
from promptpotter.shared.errors import graceful
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.application.recon.recon_report import ReconBrief
    from promptpotter.domain.analysis import FailureAnalysis
    from promptpotter.infrastructure.tracing import ObservabilityBridge

logger = logging.getLogger(__name__)

__all__ = ["l1_generate", "validate_overrides"]


def validate_overrides(
    node_overrides: dict[str, dict],
    pipeline_schema: PipelineSchema,
) -> list[ValidationFailure]:
    """Walk node-scoped pipeline_params overrides against user-declared
    allowed sets. Today only ``model`` has an allowed set
    (``PipelineSchema.available_models``); future enum params plug in here.

    Failures are NOT silently dropped. The caller (``l1_generate``)
    attaches the returned list to the candidate's OptSearchPoint memory,
    where it acts as a structural property of the SearchPoint and triggers
    the synthetic-0 early exit in ``score_search_point()``. See
    ``docs/architecture/optimization.md``.
    """
    failures: list[ValidationFailure] = []
    if not pipeline_schema.available_models:
        return failures
    allowed_models = list(pipeline_schema.available_models)
    allowed_models_set = set(allowed_models)
    for node_name, node_params in node_overrides.items():
        if not isinstance(node_params, dict):
            continue
        proposed = node_params.get("model")
        if proposed is not None and proposed not in allowed_models_set:
            failures.append(
                ValidationFailure(
                    axis=f"{node_name}.model",
                    value=str(proposed),
                    allowed=allowed_models,
                    reason="not_in_available_models",
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
            if params:
                param_parts = []
                for p in sorted(params):
                    desc = descs.get(p)
                    param_parts.append(f"{p} ({desc})" if desc else p)
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
    recon_brief: ReconBrief | None = None,
    is_probe_round: bool = False,
    scan_compact: bool = False,
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
            L1PromptData(
                task_context=opt_sp.task_context or None,
                critique_text=opt_sp.memory.critique_text,
                l2_directive=opt_sp.memory.l2_directive,
                thinking_styles=opt_sp.memory.thinking_styles or None,
                plan=opt_sp.plan or "",
                warning_inventory=opt_sp.memory.warning_inventory or None,
                escalation_journal=opt_sp.memory.escalation_journal or None,
                is_probe_round=is_probe_round,
                recon_brief=recon_brief,
                scan_compact=scan_compact,
                failure_analysis=failure_analysis,
                search_memory_digest=search_memory_digest,
                pipeline_schema_text=schema_text,
            )
        ),
    }
    _template = load_optimizer_prompt("meta_scan_aware")
    meta_prompt = _template.compile_prompt(**_compile_vars)

    response = await llm_call(
        llm_client,
        messages=[{"role": "user", "content": meta_prompt}],
        node="l1_generate",
        model=model,
        temperature=creativity,
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

        # Validate remaining overrides against allowed-value sets. Failures
        # are recorded as a property of the SearchPoint (NOT silently dropped)
        # so score_search_point() can short-circuit to a synthetic 0. See
        # docs/architecture/optimization.md.
        validation_failures: list[ValidationFailure] = []
        if pipeline_schema:
            validation_failures = validate_overrides(node_overrides, pipeline_schema)
            for vf in validation_failures:
                logger.warning(
                    "l1_generate: validation failure on %s — proposed %r not in allowed %r",
                    vf.axis,
                    vf.value,
                    vf.allowed,
                )

        child = opt_sp.derive_candidate(
            changes_description=v.get("changes_description", ""),
            **prompt_changes,
        )
        if tc_changes:
            child.task_context = child.task_context.merge(tc_changes)
        if validation_failures:
            child.memory.validation_failures = list(validation_failures)
        c_dict = child.prompt_field_dict()
        c_dict["id"] = child.id
        c_dict["parent_id"] = child.parent_id
        c_dict["changes_description"] = child.changes_description
        if node_overrides:
            c_dict["__pipeline_params_override__"] = node_overrides
        if validation_failures:
            c_dict["__validation_failures__"] = [vf.to_dict() for vf in validation_failures]
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
