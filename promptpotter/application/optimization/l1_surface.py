"""L1-generate section renderers + L2-visible field catalogue.

The only layer with an override channel: L2 writes
``l1_section_overrides`` (visibility) / ``l1_section_overrides_text``
(replacement) onto the OSP; ``compile_prompt_vars`` honours both before
falling back to the registered renderer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from promptpotter.application.optimization.dispatch import (
    SECTION_L2_DIRECTIVE,
    SECTION_PLAN,
    DispatchState,
    Layer,
    get_layer_configs,
)
from promptpotter.application.optimization.formatting import (
    format_axis_digest_block,
    summarize_warning_inventory,
)
from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    "L1_SCALAR_DESCRIPTIONS",
    "L1_SECTION_DESCRIPTIONS",
    "L1_SECTION_RENDERERS",
    "format_l1_generate_field_catalogue",
]


_L1_AXIS_LABELS: dict[str, str] = {
    "failure_clusters": "Common failure patterns",
    "dead_queries": "Dead queries (never hit)",
    "top_axes": "High-impact axes",
    "top_values": "Best-performing values",
}

_TASK_CONTEXT_SKIP = frozenset({"raw_description", "upstream_context", "downstream_context"})

_ENUM_RENDER_CAP = 12


def _render_schema_text(pipeline_schema: PipelineSchema) -> str:
    """Pipeline schema for L1 context. Enums capped at ``_ENUM_RENDER_CAP``."""
    lines: list[str] = []
    npk = pipeline_schema.node_param_keys()
    if not npk:
        return ""

    lines.append(
        "VALID PIPELINE NODES AND PARAMETERS (only use these — do not invent nodes or params):"
    )
    for node_name, params in npk.items():
        node = pipeline_schema.get_node(node_name)
        descs = node.param_descriptions if node else {}
        enums = node.param_allowed_values if node else {}
        if not params:
            lines.append(f"  {node_name}: (no tunable params)")
            continue
        param_parts: list[str] = []
        for p in sorted(params):
            desc = descs.get(p)
            allowed = enums.get(p)
            suffix_bits: list[str] = []
            if desc:
                suffix_bits.append(desc)
            if allowed:
                shown = list(allowed)[:_ENUM_RENDER_CAP]
                extra = len(allowed) - len(shown)
                suffix = "one of: " + ", ".join(shown)
                if extra > 0:
                    suffix += f" (+{extra} more)"
                suffix_bits.append(suffix)
            param_parts.append(f"{p} ({'; '.join(suffix_bits)})" if suffix_bits else p)
        lines.append(f"  {node_name}: {', '.join(param_parts)}")

    mutable = [n for n in pipeline_schema.nodes if n.output_schema and n.output_schema.fields]
    if mutable:
        lines.append("")
        lines.append(
            "OUTPUT SCHEMA MUTATIONS — use as output_schema param: "
            '["+", name, type, is_array, desc] / ["-", name] / '
            '["~", old, new, type, is_array, desc]'
        )
        for node in mutable:
            assert node.output_schema is not None
            lines.append(f"  {node.name}: {', '.join(node.output_schema.fields)}")

    text = "\n".join(lines)
    if pipeline_schema.available_models:
        text += "\n\nAVAILABLE MODELS (only use these for model overrides):\n"
        text += "\n".join(f"  {m}" for m in pipeline_schema.available_models)
    return text


# ---------------------------------------------------------------------------
# Section renderers — uniform ``(ctx: DispatchState) -> str`` signature.
# ---------------------------------------------------------------------------


def _section_pipeline_schema_text(ctx: DispatchState) -> str:
    return _render_schema_text(ctx.pipeline_schema) if ctx.pipeline_schema else ""


def _section_failure_analysis(ctx: DispatchState) -> str:
    fa = ctx.opt_sp.failure_analysis
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


def _section_axes_l1(ctx: DispatchState) -> str:
    return (
        format_axis_digest_block(ctx.axis_digest, _L1_AXIS_LABELS, header="HISTORICAL CONTEXT:")
        if ctx.axis_digest
        else ""
    )


def _section_task_context(ctx: DispatchState) -> str:
    tc = ctx.opt_sp.task_context
    if not tc:
        return ""
    lines = "\n".join(
        f"  {k}: {val}" for k, val in tc.items() if val and k not in _TASK_CONTEXT_SKIP
    )
    return f"CONTEXT:\n{lines}" if lines else ""


def _section_escalation_probe(ctx: DispatchState) -> str:
    """Probe-round per-query warning block — fires only when probe AND log present."""
    if not ctx.probe_next_round:
        return ""
    log = ctx.opt_sp.escalation_log
    if not log:
        return ""
    lines = [
        "PROBE ROUND: queries have recurring pipeline warnings. "
        "Generate candidates that address pipeline robustness."
    ]
    warning_inventory = ctx.opt_sp.warning_inventory or None
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
            tried = [ej for ej in log if ej.get("problem_step") == dom_step][-3:]
            if tried:
                lines.append(f"Previous attempts at {dom_step}:")
                lines.extend(
                    f"  - {ej.get('degraded_rate', 0):.0%} degraded, {ej.get('warning_types', {})}"
                    for ej in tried
                )
    return "\n".join(lines)


def _section_escalation_alert(ctx: DispatchState) -> str:
    """Non-probe aggregated alert — suppressed by an active l2_directive."""
    if ctx.probe_next_round:
        return ""
    if ctx.opt_sp.l2_directive:
        return ""
    log = ctx.opt_sp.escalation_log
    if not log:
        return ""
    latest = log[-1]
    alert = [
        f"PIPELINE ISSUE: {latest.get('degraded_rate', 0):.0%} of queries "
        f"degrade at {latest.get('problem_step', 'unknown')}. "
        "Address pipeline instability."
    ]
    if len(log) > 1:
        alert.append(f"{len(log)} prior attempts unresolved.")
    if latest.get("warning_types"):
        alert.append(f"Warnings: {latest['warning_types']}")
    return "\n".join(alert)


# ---------------------------------------------------------------------------
# L1 surface registry — drives ``LAYER_CONFIGS[Layer.L1_GENERATE]``.
# ---------------------------------------------------------------------------


L1_SECTION_RENDERERS: dict[str, Callable[[DispatchState], str]] = {
    "pipeline_schema_text": _section_pipeline_schema_text,
    "failure_analysis": _section_failure_analysis,
    "axes_l1": _section_axes_l1,
    "task_context": _section_task_context,
    "escalation_probe": _section_escalation_probe,
    "escalation_alert": _section_escalation_alert,
    "l2_directive": SECTION_L2_DIRECTIVE,
    "plan": SECTION_PLAN,
}


L1_SECTION_DESCRIPTIONS: dict[str, str] = {
    "pipeline_schema_text": "Target pipeline + active steps + per-node schema.",
    "failure_analysis": "Latest round's clustered failure patterns.",
    "axes_l1": "AxisIndex digest for L1-generate (failure clusters, top axes).",
    "task_context": "Structured domain context — domain, goals, challenges.",
    "escalation_probe": "Probe-round per-query warning block (probe rounds only).",
    "escalation_alert": "Aggregated pipeline-issue alert (non-probe).",
    "l2_directive": "L2's strategic directive for this round.",
    "plan": "L3's strategic framework.",
}

L1_SCALAR_DESCRIPTIONS: dict[str, str] = {
    "n_variants": "[scalar] How many candidates L1 must produce.",
    "accuracy_pct": "[scalar] Current accuracy of the parent SearchPoint.",
    "n_queries": "[scalar] Number of queries the parent was scored on.",
    "rendered_prompt": "[scalar] The current prompt being optimized.",
}


def format_l1_generate_field_catalogue(
    overrides_visible: dict[str, bool],
    overrides_text: dict[str, str],
) -> str:
    """Render the L1-generate field catalogue for L2's prompt.

    One line per entry: ``[ON]``/``[OFF]``/``[scalar]`` + name + description
    (+ override-text preview when set). Sections come from
    ``LAYER_CONFIGS[Layer.L1_GENERATE].sections``; scalars from
    ``L1_SCALAR_DESCRIPTIONS``. L2 always sees every variable that exists
    in code — including sections currently toggled off.
    """
    # Touch the configs registry so any new L1 section added in code (but
    # not yet entered in L1_SECTION_DESCRIPTIONS) trips a clear lookup
    # error rather than silently disappearing.
    _ = get_layer_configs()[Layer.L1_GENERATE].sections.keys()
    lines: list[str] = []
    for name, desc in L1_SECTION_DESCRIPTIONS.items():
        state = "[OFF]" if overrides_visible.get(name) is False else "[ON]"
        line = f"  {state} {name} — {desc}"
        if name in overrides_text:
            preview = overrides_text[name].strip().splitlines()[0][:80]
            line += f"\n    override: {preview!r}"
        lines.append(line)
    for name, desc in L1_SCALAR_DESCRIPTIONS.items():
        lines.append(f"  [scalar] {name} — {desc}")
    return "\n".join(lines)
