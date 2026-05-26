"""`INJECTIONS` registry — the one dict every prompt site resolves against."""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.bundle import (
    InjectionKind,
    _Injection,
    accessor_renderer,
)
from promptpotter.application.optimization.dispatch.hub.injections.catalogues import (
    _r_l1_signal_catalogue,
    _r_pipeline_param_catalogue,
)
from promptpotter.application.optimization.dispatch.hub.injections.layer_state import (
    _r_critique,
    _r_l1_situational_examples,
    _r_l1_supplemental_rules,
    _r_rebase_capability,
    _r_task_context,
)
from promptpotter.application.optimization.dispatch.hub.injections.panels import (
    _r_archive_top_runs,
    _r_axis_memory,
    _r_diagnostics,
    _r_intractable_samples,
    _r_origin_strengths,
    _r_rare_hit_samples,
)
from promptpotter.application.optimization.dispatch.hub.injections.wounds import (
    _r_l2_guard_breaches,
    _r_l3_guard_breaches,
    _r_runtime_failures,
    _r_validation_failures,
)

# `char_cap`: int for LLM-authored text (truncate + warn on overrun); None for derived/measurement
# (already bounded by *_RENDER_CAP) and `task_context` (per-field cap is finer).
INJECTIONS: dict[str, _Injection] = {
    "plan": _Injection(
        "plan",
        InjectionKind.TRACE,
        accessor_renderer(lambda b: b.opt_sp.plan, "PLAN:\n{value}"),
        "L3's strategic plan text. Persistent until next L3 fire.",
        char_cap=800,
    ),
    "l3_to_l2_note": _Injection(
        "l3_to_l2_note",
        InjectionKind.DIRECTIVE,
        accessor_renderer(lambda b: b.opt_sp.memory.wounds.l3_note, "L3 NOTE TO L2:\n{value}"),
        "Sticky L3→L2 pointer. Mounted only in L2's template; absent from L1.",
        char_cap=400,
    ),
    "rendered_prompt": _Injection(
        "rendered_prompt",
        InjectionKind.TRACE,
        accessor_renderer(lambda b: b.opt_sp.render(), "CURRENT PROMPT:\n---\n{value}\n---"),
        "Current best searchpoint's compiled prompt body.",
        char_cap=2500,
    ),
    "pipeline_param_catalogue": _Injection(
        "pipeline_param_catalogue",
        InjectionKind.DERIVED,
        _r_pipeline_param_catalogue,
        "Pipeline-param menu: name + ≤4-value enum hint per node, plus available models.",
        char_cap=None,
    ),
    "diagnostics": _Injection(
        "diagnostics",
        InjectionKind.DERIVED,
        _r_diagnostics,
        "Layer-agnostic round readout: STATUS header + RoundDiagnostics body.",
        char_cap=None,
    ),
    "validation_failures": _Injection(
        "validation_failures",
        InjectionKind.MEASUREMENT,
        _r_validation_failures,
        "Wound 1: L1 parse-time validator failures (per-axis, per-value).",
        char_cap=None,
    ),
    "runtime_failures": _Injection(
        "runtime_failures",
        InjectionKind.MEASUREMENT,
        _r_runtime_failures,
        "Wound 2: DegradationCheck mid-eval evidence — per-candidate runtime failures.",
        char_cap=None,
    ),
    "l2_guard_breaches": _Injection(
        "l2_guard_breaches",
        InjectionKind.MEASUREMENT,
        _r_l2_guard_breaches,
        "Wound 4: L2_CONTEXT post-parse guard outcomes; non-empty force-triggers L3 heal.",
        char_cap=None,
    ),
    "l3_guard_breaches": _Injection(
        "l3_guard_breaches",
        InjectionKind.MEASUREMENT,
        _r_l3_guard_breaches,
        "L3_PLAN post-parse guard outcomes. L3 reads its own past breaches.",
        char_cap=None,
    ),
    "task_context": _Injection(
        "task_context",
        InjectionKind.TRACE,
        _r_task_context,
        "Persistent task framing dict refined by L2; broadcast to all four prompts.",
        char_cap=None,  # _r_task_context caps each field at TASK_CONTEXT_VALUE_CAP
    ),
    "critique": _Injection(
        "critique",
        InjectionKind.TRACE,
        _r_critique,
        "Compact view of the most recent L1_CRITIQUE LLM output dict.",
        char_cap=800,
    ),
    "l1_overrides": _Injection(
        "l1_overrides",
        InjectionKind.TRACE,
        accessor_renderer(
            lambda b: b.opt_sp.memory.l1_overrides, "CURRENT L1 CONFIG: {value}", json_value=True
        ),
        "Current L1 runtime knobs (creativity, n_variants, etc.) as JSON.",
        char_cap=None,
    ),
    "l1_signal_catalogue": _Injection(
        "l1_signal_catalogue",
        InjectionKind.DERIVED,
        _r_l1_signal_catalogue,
        "L1 SIGNAL MENU: sorted L1_POSSIBLE placeholder names L2 may use in l1_layout.",
        char_cap=None,
    ),
    "axis_memory": _Injection(
        "axis_memory",
        InjectionKind.DERIVED,
        _r_axis_memory,
        "Cross-cycle axis-keyed digest from AxisIndex: rankings, persistent failures, "
        "failure clusters, value trends, exhausted axes.",
        char_cap=None,
    ),
    "origin_strengths": _Injection(
        "origin_strengths",
        InjectionKind.MEASUREMENT,
        _r_origin_strengths,
        "Round-0 origin's per-sample hits — the floor variants must preserve.",
        char_cap=None,
    ),
    "intractable_samples": _Injection(
        "intractable_samples",
        InjectionKind.MEASUREMENT,
        _r_intractable_samples,
        "Cumulative cycle-wide miss set — samples no candidate has solved yet this cycle.",
        char_cap=None,
    ),
    "archive_top_runs": _Injection(
        "archive_top_runs",
        InjectionKind.MEASUREMENT,
        _r_archive_top_runs,
        "Top-K historical runs across the dataset's archive — anchor the optimizer "
        "against the best composite ever scored instead of re-discovering it.",
        char_cap=None,
    ),
    "rare_hit_samples": _Injection(
        "rare_hit_samples",
        InjectionKind.MEASUREMENT,
        _r_rare_hit_samples,
        "Samples cracked by ≤3 of ≥10 attempts — names the run(s) that hit them "
        "(recipe pointers). Zero-hit samples surface as capacity-bound.",
        char_cap=None,
    ),
    "l1_supplemental_rules": _Injection(
        "l1_supplemental_rules",
        InjectionKind.DIRECTIVE,
        _r_l1_supplemental_rules,
        "Situational rules appended to L1's instruction — auto-triggered from "
        "bundle state (PEAKED axes, runtime failures, chain-bind, continuous-axis, "
        "L2 stall, LaTeX corruption) plus L2-authored entries on opt_sp.",
        char_cap=1000,
    ),
    "l1_situational_examples": _Injection(
        "l1_situational_examples",
        InjectionKind.DIRECTIVE,
        _r_l1_situational_examples,
        "Worked examples pinned to currently-active triggers — built-ins shipped "
        "in auto_rules.py plus L2-authored entries on opt_sp. Examples whose "
        "trigger is not active this round are silently filtered.",
        char_cap=1000,
    ),
    "rebase_capability": _Injection(
        "rebase_capability",
        InjectionKind.DIRECTIVE,
        _r_rebase_capability,
        "Conditional fork_proposal escape-hatch instruction (renders into L2 + "
        "L3 prompts). Empty when ``OptimizationConfig.rebase_capability`` is "
        "off — keeps prompt body bit-for-bit identical to a no-rebase "
        "ablation so the input distribution doesn't drift on prompt text.",
        char_cap=None,
    ),
}
