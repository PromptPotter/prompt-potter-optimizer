"""Inbox registry — single declarative catalogue of optimizer-prompt inputs.

Every L1/L2 prompt receives an ``{{inbox}}`` block assembled here. Each
section is one function ``(cycle, **kwargs) -> str`` that returns its
rendered text or ``""`` when not applicable. :func:`assemble_inbox` walks
:data:`LAYER_ORDER` for the target layer, calls each section, and joins
the non-empty results. On L1 the L2 directive supersedes the L1 critique
when both are populated (sliding window of 1).

The critique layer keeps its own assembler (``critique.py``) because its
sections share cross-cutting state (anomalies, near_miss). L3 keeps its
multi-hole template; only the intelligence block flows through this
registry.
"""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.optimization.nodes.formatting import (
    format_axis_digest_block,
    format_escalation_report,
    format_runtime_failure_line,
    summarize_warning_inventory,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle


class Layer(enum.StrEnum):
    """Optimizer layer that consumes an inbox."""

    L1 = "L1"
    L2 = "L2"


_L1_AXIS_LABELS: dict[str, str] = {
    "failure_clusters": "Common failure patterns",
    "dead_queries": "Dead queries (never hit)",
    "top_axes": "High-impact axes",
    "top_values": "Best-performing values",
}
_L2_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_group_insights": "Failure group x axis",
    "persistent_failures": "Persistent failures",
    "volatile_queries": "Volatile queries (oscillating)",
}

_TASK_CONTEXT_SKIP = frozenset({"raw_description", "upstream_context", "downstream_context"})


# ---------------------------------------------------------------------------
# Section renderers — each returns the section text or "" when inactive.
# Layer is passed via kwarg so a single section can label itself differently
# under different layers (only ``_section_l2_directive`` uses this today).
# ---------------------------------------------------------------------------


def _section_pipeline_schema_text(
    _cycle: Cycle, *, pipeline_schema_text: str = "", **_: object
) -> str:
    return pipeline_schema_text or ""


def _section_failure_analysis(cycle: Cycle, **_: object) -> str:
    fa = cycle.opt_sp.failure_analysis
    if not fa or not fa.patterns:
        return ""
    lines = [f"FAILURE ANALYSIS ({fa.total_failures} failures / {fa.total_results} total):"]
    for i, pat in enumerate(fa.patterns[:2], 1):
        lines.append(f"  {i}. {pat.name} — {pat.query_count} queries ({pat.fraction:.0%})")
        if pat.example_queries:
            lines.append(f'     Example: "{pat.example_queries[0][:60]}"')
        sig = {
            k: v for k, v in pat.signals.items() if k not in ("error", "degraded", "total_time_ms")
        }
        if sig:
            sig_str = ", ".join(f"{k}={v}" for k, v in list(sig.items())[:2])
            lines.append(f"     Signals: {sig_str}")
    return "\n".join(lines)


def _section_axes_l1(cycle: Cycle, **_: object) -> str:
    if cycle.axes is None:
        return ""
    digest = cycle.axes.digest_for_l1_generate()
    return format_axis_digest_block(digest, _L1_AXIS_LABELS) if digest else ""


def _section_task_context(cycle: Cycle, **_: object) -> str:
    tc = cycle.opt_sp.task_context
    if not tc:
        return ""
    lines = "\n".join(
        f"  {k}: {val}" for k, val in tc.items() if val and k not in _TASK_CONTEXT_SKIP
    )
    return f"CONTEXT:\n{lines}" if lines else ""


def _section_escalation_probe(cycle: Cycle, **_: object) -> str:
    """Probe-round per-query warning block — fires only when probe AND journal present."""
    if not cycle.probe_next_round:
        return ""
    journal = cycle.opt_sp.escalation_journal
    if not journal:
        return ""
    lines = [
        "PROBE ROUND: queries have recurring pipeline warnings. "
        "Generate candidates that address pipeline robustness."
    ]
    warning_inventory = cycle.opt_sp.warning_inventory or None
    if warning_inventory:
        inv = summarize_warning_inventory(warning_inventory)
        if inv:
            lines.extend(["", inv])
        step_counts: Counter[str] = Counter()
        for entry in warning_inventory.values():
            for wtype in entry.get("warnings", {}):
                step_counts[wtype.split(":", 1)[0]] += 1
        if step_counts:
            dom_step, dom_count = step_counts.most_common(1)[0]
            lines.append(f"\nDominant step: {dom_step} ({dom_count} warnings)")
            tried = [ej for ej in journal if ej.get("problem_step") == dom_step][-3:]
            if tried:
                lines.append(f"Previous attempts at {dom_step}:")
                lines.extend(
                    f"  - {ej.get('degraded_rate', 0):.0%} degraded, {ej.get('warning_types', {})}"
                    for ej in tried
                )
    return "\n".join(lines)


def _section_escalation_alert(cycle: Cycle, **_: object) -> str:
    """Non-probe aggregated alert — suppressed by an active l2_directive."""
    if cycle.probe_next_round:
        return ""
    if cycle.opt_sp.l2_directive:
        return ""
    journal = cycle.opt_sp.escalation_journal
    if not journal:
        return ""
    latest = journal[-1]
    alert = [
        f"PIPELINE ISSUE: {latest.get('degraded_rate', 0):.0%} of queries "
        f"degrade at {latest.get('problem_step', 'unknown')}. "
        "Address pipeline instability."
    ]
    if len(journal) > 1:
        alert.append(f"{len(journal)} prior attempts unresolved.")
    if latest.get("warning_types"):
        alert.append(f"Warnings: {latest['warning_types']}")
    return "\n".join(alert)


def _section_l2_directive(cycle: Cycle, *, layer: Layer, **_: object) -> str:
    v = cycle.opt_sp.l2_directive
    if not v:
        return ""
    label = "DIRECTIVE:" if layer is Layer.L1 else "PREVIOUS DIRECTIVE:"
    return f"{label}\n{v}"


def _section_l1_critique_text(cycle: Cycle, **_: object) -> str:
    v = cycle.opt_sp.l1_critique_text
    return f"CRITIQUE:\n{v}" if v else ""


def _section_plan(cycle: Cycle, **_: object) -> str:
    v = cycle.opt_sp.plan
    return f"PLAN:\n{v}" if v else ""


def _escalation_report_text(
    cycle: Cycle, escalation_check_result: dict | None, pipeline_params: dict | None
) -> str:
    if not escalation_check_result:
        return ""
    schema = cycle.session.pipeline_schema if cycle.session is not None else None
    text = format_escalation_report(
        escalation_check_result,
        cycle.opt_sp.escalation_journal or None,
        pipeline_params,
        pipeline_schema=schema,
    )
    return text or ""


def _section_escalation_section(
    cycle: Cycle,
    *,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    **_: object,
) -> str:
    return _escalation_report_text(cycle, escalation_check_result, pipeline_params)


def _section_warning_inventory(
    cycle: Cycle,
    *,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    **_: object,
) -> str:
    """L2 fallback: per-query warning inventory when no escalation section."""
    if _escalation_report_text(cycle, escalation_check_result, pipeline_params):
        return ""
    inventory = cycle.opt_sp.warning_inventory
    if not inventory:
        return ""
    text = summarize_warning_inventory(inventory)
    return (text + "\n") if text else ""


def _section_validation_failures(
    _cycle: Cycle,
    *,
    candidate_scores: list[dict] | None = None,
    **_: object,
) -> str:
    vfs: list[dict] = []
    for cs in candidate_scores or []:
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
        "  ↳ Required L2 action: produce a directive that names the disallowed "
        "value(s) explicitly and instructs L1 to choose only from the allowed "
        'set. Example: "For llm_only.model, use ONLY one of: <list>. Do NOT '
        'propose any other value such as gpt-4o." Self-healing depends on the '
        "directive being explicit."
    )
    return "\n".join(lines)


def _section_runtime_failures(cycle: Cycle, *, round_num: int = 0, **_: object) -> str:
    rfs = [rf.to_dict() for rf in cycle.opt_sp.runtime_failures]
    if not rfs:
        return ""
    rfs_new = [rf for rf in rfs if rf.get("first_seen_round", 0) == round_num]
    rfs_acc = [rf for rf in rfs if rf.get("first_seen_round", 0) != round_num]
    lines = [
        "RUNTIME FAILURES — L2 SELF-HEALING EVIDENCE",
        "  (candidates ran but produced high warning rates; L2 must adjust "
        "its own strategy — directive, task_context, optimizer_params — to "
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
        "  ↳ Required L2 action: this is L2 self-healing, not L1 correction. "
        "Update your OWN outputs — tighten the directive to name the failing "
        "config range, refine task_context with the discovered constraint, or "
        "adjust optimizer_params (creativity, n_variants) to narrow L1's search "
        "around the safe region. Do NOT just parrot 'don't use X' to L1 — "
        'restructure the search. Example directive: "Reasoning models on this '
        "task need max_tokens ≥ 2000 to emit a final answer; propose variants "
        'only within that range." '
        "If ACCUMULATED entries persist across multiple L2 attempts, L3 will "
        "replan the pipeline itself next."
    )
    return "\n".join(lines)


def _section_axes_l2(cycle: Cycle, **_: object) -> str:
    if cycle.axes is None:
        return ""
    digest = cycle.axes.digest_for_l2()
    return format_axis_digest_block(digest, _L2_AXIS_LABELS) if digest else ""


# ---------------------------------------------------------------------------
# Per-layer order — drives both registration filter and output sequence.
# ---------------------------------------------------------------------------


_SECTIONS: dict[str, Callable[..., str]] = {
    "pipeline_schema_text": _section_pipeline_schema_text,
    "failure_analysis": _section_failure_analysis,
    "axes_l1": _section_axes_l1,
    "task_context": _section_task_context,
    "escalation_probe": _section_escalation_probe,
    "escalation_alert": _section_escalation_alert,
    "l2_directive": _section_l2_directive,
    "l1_critique_text": _section_l1_critique_text,
    "plan": _section_plan,
    "escalation_section": _section_escalation_section,
    "warning_inventory": _section_warning_inventory,
    "validation_failures": _section_validation_failures,
    "runtime_failures": _section_runtime_failures,
    "axes_l2": _section_axes_l2,
}


LAYER_ORDER: dict[Layer, tuple[str, ...]] = {
    Layer.L1: (
        "pipeline_schema_text",
        "failure_analysis",
        "axes_l1",
        "task_context",
        "escalation_probe",
        "escalation_alert",
        "l2_directive",
        "l1_critique_text",
        "plan",
    ),
    Layer.L2: (
        "escalation_section",
        "warning_inventory",
        "l1_critique_text",
        "l2_directive",
        "validation_failures",
        "runtime_failures",
        "axes_l2",
    ),
}


def assemble_inbox(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
) -> str:
    """Walk the registry for *layer*, render each section, drop empties, join.

    Reads persistent state from *cycle* (``cycle.opt_sp``,
    ``cycle.axes``, ``cycle.probe_next_round``,
    ``cycle.session.pipeline_schema``). Transient per-call inputs ride
    along as kwargs.

    On L1 the L2 directive supersedes the L1 critique whenever both are
    populated (the directive is L2's digested view of the critique —
    sliding window of 1). Returns ``""`` when no section produces content.
    """
    kwargs: dict[str, object] = {
        "round_num": round_num,
        "pipeline_schema_text": pipeline_schema_text,
        "candidate_scores": candidate_scores,
        "escalation_check_result": escalation_check_result,
        "pipeline_params": pipeline_params,
        "layer": layer,
    }
    sections: dict[str, str] = {}
    for name in LAYER_ORDER[layer]:
        text = _SECTIONS[name](cycle, **kwargs)
        if text:
            sections[name] = text

    # On L1 the L2 directive replaces the critique whenever both are present.
    if layer is Layer.L1 and "l2_directive" in sections:
        sections.pop("l1_critique_text", None)

    return "\n\n".join(sections[name] for name in LAYER_ORDER[layer] if name in sections)


__all__ = [
    "LAYER_ORDER",
    "Layer",
    "assemble_inbox",
]
