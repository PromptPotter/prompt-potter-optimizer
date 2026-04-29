"""Dispatch-message registry — single declarative catalogue of optimizer-prompt inputs.

Every L1 (generate/critique) / L2 / L3 prompt receives a ``{{dispatch_msg}}``
block assembled here. The flow is **five nouns**:

    archive → AxisIndex (cached) → LayerContext (per-call payload) →
    sections (pure formatters) → dispatch_msg

A :class:`LayerContext` is built once per call (:func:`compile_layer_context`)
holding every input a section may need: the persistent ``cycle`` reference,
the per-call kwargs (round_num, scoring_result, …), the layer-appropriate
axis digest pre-fetched from ``cycle.axes``, and an optional
:class:`_CritiqueContext` populated only on L1_CRITIQUE.

Each section is ``(ctx: LayerContext) -> str`` — pure consumer, no I/O,
no cycle access except via ``ctx.cycle``. :func:`assemble_dispatch_msg`
walks :data:`LAYER_ORDER` for the target layer, calls each section, drops
empties, joins. On L1_GENERATE the L2 directive supersedes the L1
critique when both are populated (sliding window of 1).
"""

from __future__ import annotations

import enum
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.optimization.nodes.dispatch_l1c_sections import (
    L1C_SECTION_ORDER,
    L1C_SECTIONS,
    CritiqueContext,
    compile_critique_context,
)
from promptpotter.application.optimization.nodes.formatting import (
    format_axis_digest_block,
    format_escalation_report,
    format_runtime_failure_line,
    summarize_warning_inventory,
)

if TYPE_CHECKING:
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.nodes.l1.score import L1ScoringResult
    from promptpotter.domain.pipeline_schema import PipelineSchema


class Layer(enum.StrEnum):
    """Optimizer layer that consumes a dispatch_msg."""

    L1_GENERATE = "L1_GENERATE"
    L1_CRITIQUE = "L1_CRITIQUE"
    L2 = "L2"
    L3 = "L3"


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
_L3_AXIS_LABELS: dict[str, str] = {
    "axis_rankings": "Axis impact rankings",
    "bottleneck_distribution": "Bottleneck distribution",
    "failure_clusters": "Failure clusters",
    "persistent_failures": "Persistent failures",
}

_TASK_CONTEXT_SKIP = frozenset({"raw_description", "upstream_context", "downstream_context"})


# ---------------------------------------------------------------------------
# Per-call payload — single declarative bundle of every input a section reads.
# ---------------------------------------------------------------------------


@dataclass
class LayerContext:
    """Per-call payload passed to every section renderer.

    Holds everything a section may read: the persistent ``cycle`` (state
    that survives across calls — ``cycle.opt_sp``, ``cycle.axes``,
    ``cycle.rounds``), the per-call inputs (round_num, scoring_result,
    candidate_scores, …), the pre-computed ``axis_digest`` for this
    layer, and an optional ``critique`` block populated only on
    L1_CRITIQUE. Sections are ``(ctx: LayerContext) -> str`` — pure
    consumers; they never reach into ``cycle.axes`` for digests directly,
    nor recompute critique facts.
    """

    cycle: Cycle
    layer: Layer
    round_num: int = 0
    pipeline_schema_text: str = ""
    pipeline_schema: PipelineSchema | None = None
    pipeline_params: dict | None = None
    candidate_scores: list[dict] | None = None
    escalation_check_result: dict | None = None
    scoring_result: L1ScoringResult | None = None
    axis_digest: dict[str, str] | None = None
    critique: CritiqueContext | None = None


def _layer_axis_digest(layer: Layer, cycle: Cycle) -> dict[str, str] | None:
    """Pre-fetch the layer-appropriate axis digest from ``cycle.axes``."""
    if cycle.axes is None:
        return None
    if layer is Layer.L1_GENERATE:
        return cycle.axes.digest_for_l1_generate()
    if layer is Layer.L1_CRITIQUE:
        return cycle.axes.digest_for_l1_critique()
    if layer is Layer.L2:
        return cycle.axes.digest_for_l2()
    if layer is Layer.L3:
        return cycle.axes.digest_for_l3()
    return None


def compile_layer_context(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    pipeline_schema: PipelineSchema | None = None,
    pipeline_params: dict | None = None,
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    scoring_result: L1ScoringResult | None = None,
) -> LayerContext:
    """Build the per-call :class:`LayerContext` for *layer* over *cycle*.

    Pre-fetches the layer-appropriate axis digest and (for L1_CRITIQUE
    only, when ``scoring_result`` is provided) the cross-cutting
    critique facts. Both happen exactly once per call; sections then
    consume the prepared ctx without re-deriving.
    """
    critique: CritiqueContext | None = None
    if layer is Layer.L1_CRITIQUE and scoring_result is not None:
        critique = compile_critique_context(cycle, scoring_result, pipeline_schema)
    return LayerContext(
        cycle=cycle,
        layer=layer,
        round_num=round_num,
        pipeline_schema_text=pipeline_schema_text,
        pipeline_schema=pipeline_schema,
        pipeline_params=pipeline_params,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
        scoring_result=scoring_result,
        axis_digest=_layer_axis_digest(layer, cycle),
        critique=critique,
    )


# ---------------------------------------------------------------------------
# Section renderers — each returns the section text or "" when inactive.
# Signature is uniformly ``(ctx: LayerContext) -> str``; sections read from
# ``ctx.cycle`` (persistent state) and per-call fields on ``ctx``. Layer-
# aware labelling reads ``ctx.layer`` (only ``_section_l2_directive`` uses
# this today). L1_CRITIQUE-only sections live in ``dispatch_l1c_sections``.
# ---------------------------------------------------------------------------


def _section_pipeline_schema_text(ctx: LayerContext) -> str:
    return ctx.pipeline_schema_text or ""


def _section_failure_analysis(ctx: LayerContext) -> str:
    fa = ctx.cycle.opt_sp.failure_analysis
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


def _section_axes_l1(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L1_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


def _section_task_context(ctx: LayerContext) -> str:
    tc = ctx.cycle.opt_sp.task_context
    if not tc:
        return ""
    lines = "\n".join(
        f"  {k}: {val}" for k, val in tc.items() if val and k not in _TASK_CONTEXT_SKIP
    )
    return f"CONTEXT:\n{lines}" if lines else ""


def _section_escalation_probe(ctx: LayerContext) -> str:
    """Probe-round per-query warning block — fires only when probe AND journal present."""
    cycle = ctx.cycle
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


def _section_escalation_alert(ctx: LayerContext) -> str:
    """Non-probe aggregated alert — suppressed by an active l2_directive."""
    cycle = ctx.cycle
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


def _section_l2_directive(ctx: LayerContext) -> str:
    v = ctx.cycle.opt_sp.l2_directive
    if not v:
        return ""
    label = "DIRECTIVE:" if ctx.layer is Layer.L1_GENERATE else "PREVIOUS DIRECTIVE:"
    return f"{label}\n{v}"


def _section_l1_critique_text(ctx: LayerContext) -> str:
    v = ctx.cycle.opt_sp.l1_critique_text
    return f"CRITIQUE:\n{v}" if v else ""


def _section_plan(ctx: LayerContext) -> str:
    v = ctx.cycle.opt_sp.plan
    return f"PLAN:\n{v}" if v else ""


def _escalation_report_text(ctx: LayerContext) -> str:
    if not ctx.escalation_check_result:
        return ""
    cycle = ctx.cycle
    schema = cycle.session.pipeline_schema
    text = format_escalation_report(
        ctx.escalation_check_result,
        cycle.opt_sp.escalation_journal or None,
        ctx.pipeline_params,
        pipeline_schema=schema,
    )
    return text or ""


def _section_escalation_section(ctx: LayerContext) -> str:
    return _escalation_report_text(ctx)


def _section_warning_inventory(ctx: LayerContext) -> str:
    """L2 fallback: per-query warning inventory when no escalation section."""
    if _escalation_report_text(ctx):
        return ""
    inventory = ctx.cycle.opt_sp.warning_inventory
    if not inventory:
        return ""
    text = summarize_warning_inventory(inventory)
    return (text + "\n") if text else ""


def _section_validation_failures(ctx: LayerContext) -> str:
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
        "  ↳ Required L2 action: produce a directive that names the disallowed "
        "value(s) explicitly and instructs L1 to choose only from the allowed "
        'set. Example: "For llm_only.model, use ONLY one of: <list>. Do NOT '
        'propose any other value such as gpt-4o." Self-healing depends on the '
        "directive being explicit."
    )
    return "\n".join(lines)


def _section_runtime_failures(ctx: LayerContext) -> str:
    rfs = [rf.to_dict() for rf in ctx.cycle.opt_sp.runtime_failures]
    if not rfs:
        return ""
    rfs_new = [rf for rf in rfs if rf.get("first_seen_round", 0) == ctx.round_num]
    rfs_acc = [rf for rf in rfs if rf.get("first_seen_round", 0) != ctx.round_num]
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


def _section_axes_l2(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L2_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


def _section_axes_l3(ctx: LayerContext) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L3_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


# ---------------------------------------------------------------------------
# Per-layer order — drives both registration filter and output sequence.
# L1_CRITIQUE sections are merged in from ``dispatch_l1c_sections``.
# ---------------------------------------------------------------------------


_SECTIONS: dict[str, Callable[[LayerContext], str]] = {
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
    "axes_l3": _section_axes_l3,
    **L1C_SECTIONS,
}


LAYER_ORDER: dict[Layer, tuple[str, ...]] = {
    Layer.L1_GENERATE: (
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
    Layer.L1_CRITIQUE: L1C_SECTION_ORDER,
    Layer.L2: (
        "escalation_section",
        "warning_inventory",
        "l1_critique_text",
        "l2_directive",
        "validation_failures",
        "runtime_failures",
        "axes_l2",
    ),
    Layer.L3: ("axes_l3",),
}


def assemble_dispatch_msg(
    layer: Layer,
    cycle: Cycle,
    *,
    round_num: int = 0,
    pipeline_schema_text: str = "",
    candidate_scores: list[dict] | None = None,
    escalation_check_result: dict | None = None,
    pipeline_params: dict | None = None,
    scoring_result: L1ScoringResult | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> str:
    """Walk the registry for *layer*, render each section, drop empties, join.

    Builds the per-call :class:`LayerContext` once (which pre-fetches the
    layer-appropriate axis digest from ``cycle.axes`` and, on
    L1_CRITIQUE, computes the cross-cutting :class:`_CritiqueContext`),
    then hands it to each section in :data:`LAYER_ORDER`.

    On L1_GENERATE the L2 directive supersedes the L1 critique whenever
    both are populated (the directive is L2's digested view of the
    critique — sliding window of 1). Returns ``""`` when no section
    produces content.
    """
    ctx = compile_layer_context(
        layer,
        cycle,
        round_num=round_num,
        pipeline_schema_text=pipeline_schema_text,
        pipeline_schema=pipeline_schema,
        pipeline_params=pipeline_params,
        candidate_scores=candidate_scores,
        escalation_check_result=escalation_check_result,
        scoring_result=scoring_result,
    )
    sections: dict[str, str] = {}
    for name in LAYER_ORDER[layer]:
        if text := _SECTIONS[name](ctx):
            sections[name] = text

    # On L1_GENERATE the L2 directive replaces the critique whenever both are present.
    if layer is Layer.L1_GENERATE and "l2_directive" in sections:
        sections.pop("l1_critique_text", None)

    return "\n\n".join(sections[name] for name in LAYER_ORDER[layer] if name in sections)


__all__ = [
    "LAYER_ORDER",
    "Layer",
    "LayerContext",
    "assemble_dispatch_msg",
    "compile_layer_context",
]
