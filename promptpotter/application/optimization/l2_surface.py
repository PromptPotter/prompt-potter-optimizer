"""L2 section renderers + extras builder. L2 has no override channel."""

from __future__ import annotations

import json
from collections.abc import Callable

from promptpotter.application.optimization.dispatch_types import (
    SECTION_L2_BRIEF,
    SECTION_PLAN,
    DispatchState,
)
from promptpotter.application.optimization.formatting import (
    format_axis_digest_block,
    format_escalation_report,
    format_runtime_failure_line,
    summarize_warning_inventory,
)
from promptpotter.application.optimization.l1_surface import format_l1_generate_field_catalogue
from promptpotter.domain.opt_search_point import OptSearchPoint

__all__ = [
    "L2_SECTION_RENDERERS",
    "compile_l2_extras",
]


_L2_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_group_insights": "Failure group x axis",
    "persistent_failures": "Persistent failures",
    "volatile_queries": "Volatile queries (oscillating)",
}


def _escalation_report_text(ctx: DispatchState) -> str:
    if not ctx.escalation_check_result:
        return ""
    text = format_escalation_report(
        ctx.escalation_check_result,
        ctx.opt_sp.escalation_log or None,
        ctx.pipeline_params,
        pipeline_schema=ctx.pipeline_schema,
    )
    return text or ""


def _section_warning_inventory(ctx: DispatchState) -> str:
    """L2 fallback: per-query warning inventory when no escalation section."""
    if _escalation_report_text(ctx):
        return ""
    inventory = ctx.opt_sp.warning_inventory
    if not inventory:
        return ""
    text = summarize_warning_inventory(inventory)
    return (text + "\n") if text else ""


def _section_validation_failures(ctx: DispatchState) -> str:
    vfs: list[dict] = []
    for cs in ctx.candidate_scores or []:
        vfs.extend(cs["validation_failures"])
    if not vfs:
        return ""
    lines = ["L1 VALIDATION FAILURES (prior round produced structurally invalid candidates):"]
    for vf in vfs:
        allowed = vf.get("allowed") or []
        allowed_str = ", ".join(allowed[:5]) + (
            f" (+{len(allowed) - 5} more)" if len(allowed) > 5 else ""
        )
        lines.append(
            f"  ⚠ axis={vf.get('axis')} proposed={vf.get('value')!r} reason={vf.get('reason')}"
        )
        lines.append(f"    allowed: [{allowed_str}]")
    lines.append(
        "  ↳ Healing is gradual. Write a brief that shifts L1 toward the "
        "allowed region — pointing at the allowed set is usually enough; "
        "spelling out every forbidden value is not required. If L1 still "
        "proposes invalid values next round, the loop retriggers with the "
        "fresh evidence and you get another pass to refine."
    )
    return "\n".join(lines)


def _section_runtime_failures(ctx: DispatchState) -> str:
    rfs = [rf.to_dict() for rf in ctx.opt_sp.runtime_failures]
    if not rfs:
        return ""
    rfs_new = [rf for rf in rfs if rf.get("first_seen_round", 0) == ctx.round_num]
    rfs_acc = [rf for rf in rfs if rf.get("first_seen_round", 0) != ctx.round_num]
    lines = [
        "RUNTIME FAILURES — L2 SELF-HEALING EVIDENCE",
        "  (candidates ran but produced high warning rates; L2 must adjust "
        "its own strategy — brief, task_context, optimizer_params — to "
        "steer L1 away from the failing config region)",
    ]
    if rfs_new:
        lines.append("")
        lines.append("NEW (this round):")
        for rf in rfs_new:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    if rfs_acc:
        lines.append("")
        lines.append(
            f"ACCUMULATED (surviving from earlier rounds, {len(rfs_acc)} patterns — "
            "L2's prior strategy adjustments did NOT reduce these):"
        )
        for rf in rfs_acc:
            lines.extend(format_runtime_failure_line(rf, rf.get("candidate_label", "")))
    lines.append("")
    lines.append(
        "  ↳ Healing is gradual and self-directed. Update your OWN outputs — "
        "shift the brief, refine task_context, or adjust optimizer_params "
        "to steer L1's search toward a safer region. ACCUMULATED items are "
        "the signal that your last angle didn't take; try a different one. "
        "Don't expect a one-shot fix — if the pattern persists across L2 "
        "attempts, L3 will replan the pipeline itself."
    )
    return "\n".join(lines)


def _section_axes_l2(ctx: DispatchState) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L2_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


# ---------------------------------------------------------------------------
# L2 surface registry — drives ``LAYER_CONFIGS[Layer.L2]``.
# ---------------------------------------------------------------------------


L2_SECTION_RENDERERS: dict[str, Callable[[DispatchState], str]] = {
    "plan": SECTION_PLAN,
    "escalation_section": _escalation_report_text,
    "warning_inventory": _section_warning_inventory,
    "l2_brief": SECTION_L2_BRIEF,
    "validation_failures": _section_validation_failures,
    "runtime_failures": _section_runtime_failures,
    "axes_l2": _section_axes_l2,
}


def compile_l2_extras(opt_sp: OptSearchPoint) -> dict[str, str]:
    """Build L2's non-section template vars (no override processing, no ``\\n\\n`` suffix).

    ``current_params`` is the optimizer-param state as JSON.
    ``task_context_section`` is opt_sp.task_context rendered as a labelled
    block (with leading ``\\n\\n``); empty when no task context is set.
    ``l1_generate_field_catalogue`` is the code-derived menu of L1's surface
    — sections (from ``LAYER_CONFIGS[Layer.L1_GENERATE].sections``) plus
    scalars (from ``L1_SCALAR_DESCRIPTIONS``) — so L2 always sees every
    capability that exists in code.
    """
    task_context_section = ""
    if opt_sp.task_context:
        tc_display = {k: v for k, v in opt_sp.task_context.items() if k != "raw_description" and v}
        if tc_display:
            task_context_section = (
                "\n\nTASK CONTEXT (structured domain understanding — refine if inaccurate):\n"
                + json.dumps(tc_display, indent=2)
            )
    return {
        "current_params": json.dumps(opt_sp.optimizer_params),
        "task_context_section": task_context_section,
        "l1_generate_field_catalogue": format_l1_generate_field_catalogue(
            opt_sp.l1_section_overrides, opt_sp.l1_section_overrides_text
        ),
    }
