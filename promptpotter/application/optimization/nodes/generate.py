"""L1 candidate generation — produce pipeline-param variants via LLM meta-prompt."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.optimization.nodes.formatting import (
    L1PromptData,
    format_context_sections,
)
from promptpotter.application.optimization.pipeline import llm_call, load_optimizer_prompt
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.infrastructure.llm.client import LLMClientBase
from promptpotter.shared.constants import PROMPT_STRING_FIELDS
from promptpotter.shared.llm_parsing import extract_parsed_json

if TYPE_CHECKING:
    from promptpotter.application.search.scan_results import ScanBrief
    from promptpotter.domain.analysis import FailureAnalysis

logger = logging.getLogger(__name__)

__all__ = ["l1_generate"]


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
    scan_brief: ScanBrief | None = None,
    is_probe_round: bool = False,
    scan_compact: bool = False,
    failure_analysis: FailureAnalysis | None = None,
    search_memory_digest: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
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
                critique_text=opt_sp.critique_text,
                l2_directive=opt_sp.l2_directive,
                thinking_styles=opt_sp.thinking_styles or None,
                plan=opt_sp.plan or "",
                warning_inventory=opt_sp.warning_inventory or None,
                escalation_journal=opt_sp.escalation_journal or None,
                is_probe_round=is_probe_round,
                scan_brief=scan_brief,
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
        # Split pipeline_params_override: prompt scheme fields go to
        # derive_candidate (rendered into the prompt string), task_context
        # sub-fields update task_context, the rest stays as node-level
        # pipeline overrides (already nested).
        _TASK_CONTEXT_OVERRIDES = {"upstream_context", "downstream_context"}
        pp_override = v.get("pipeline_params_override") or {}
        pp_override.pop("steps", None)  # LLM must not override pipeline composition
        prompt_changes = {k: pp_override[k] for k in pp_override if k in PROMPT_STRING_FIELDS}
        tc_changes = {k: pp_override[k] for k in pp_override if k in _TASK_CONTEXT_OVERRIDES}
        node_overrides = {
            k: pv
            for k, pv in pp_override.items()
            if k not in PROMPT_STRING_FIELDS and k not in _TASK_CONTEXT_OVERRIDES
        }

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

            # Filter out invalid model overrides
            if pipeline_schema.available_models:
                _valid_models = set(pipeline_schema.available_models)
                for _nn, _np in node_overrides.items():
                    if (
                        isinstance(_np, dict)
                        and "model" in _np
                        and _np["model"] not in _valid_models
                    ):
                        logger.warning(
                            "l1_generate: dropping invalid model %r for %s", _np["model"], _nn
                        )
                        del _np["model"]

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

    return candidates
