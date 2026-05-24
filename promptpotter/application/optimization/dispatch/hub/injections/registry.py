"""`INJECTIONS` registry — the one dict every prompt site resolves against.
`_r_prompt_budget_status` lives here because it reads INJECTIONS (avoids a circular import).
Import-time check fails fast if any L1-mandatory placeholder is mis-tiered.
"""

from __future__ import annotations

from promptpotter.application.optimization.dispatch.hub.bundle import (
    OPTIMIZER_PROMPT_CHAR_BUDGET,
    TASK_CONTEXT_VALUE_CAP,
    InjectionBundle,
    InjectionKind,
    InjectionTier,
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
from promptpotter.domain.l1_layout import L1_MANDATORY

# Author of each char_cap-bearing injection — drives prompt-budget-status split (L2 heals YOURS).
# `task_context` is per-field capped, not whole-block; the renderer measures its fields directly.
_INJECTION_AUTHOR: dict[str, str] = {
    "l1_supplemental_rules": "L2",
    "l1_situational_examples": "L2",
    "rendered_prompt": "L1",
    "plan": "L3",
    "l3_to_l2_note": "L3",
    "critique": "L1_CRITIQUE",
}


def _r_prompt_budget_status(b: InjectionBundle) -> str:
    """L2's self-heal surface — per-injection cap + actual size, split by author so L2 trims
    YOURS. Aggregate overrun is handled by `_apply_budget`. Mounted only in l2_context.
    Sizes are pre-truncation — OVER = what the LLM actually emitted.
    """

    def _cap_line(label: str, actual: int, cap: int) -> str:
        flag = f"   OVER by {actual - cap}" if actual > cap else ""
        return f"    {label}: {actual} / {cap}{flag}"

    yours: list[str] = [
        _cap_line(f"task_context.{k}", len(v), TASK_CONTEXT_VALUE_CAP)
        for k, v in b.opt_sp.task_context.to_dict().items()
        if v and k not in {"raw_description", "upstream_context", "downstream_context"}
    ]
    others: list[str] = []
    for name, sig in INJECTIONS.items():
        if sig.char_cap is None:
            continue
        author = _INJECTION_AUTHOR.get(name, "?")
        line = _cap_line(f"{name} ({author})", len(sig.render(b)), sig.char_cap)
        (yours if author == "L2" else others).append(line)

    return "\n".join(
        [
            "PROMPT BUDGET STATUS — every injected block is length-capped; the "
            f"composed optimizer prompt is held under {OPTIMIZER_PROMPT_CHAR_BUDGET} "
            "chars. Trim any block you author that is flagged OVER — shorten the "
            "text or author fewer items.",
            "  YOURS (heal these):",
            *yours,
            "  OTHER LAYERS (handled mechanically — not yours to edit):",
            *others,
        ]
    )


# `char_cap`: int for LLM-authored text (truncate + warn on overrun); None for derived/measurement
# (already bounded by *_RENDER_CAP) and `task_context` (per-field cap is finer).
INJECTIONS: dict[str, _Injection] = {
    "plan": _Injection(
        "plan",
        InjectionKind.TRACE,
        accessor_renderer(lambda b: b.opt_sp.plan, "PLAN:\n{value}"),
        "L3's strategic plan text. Persistent until next L3 fire.",
        tier=InjectionTier.MANDATORY,
        char_cap=800,
    ),
    "l3_to_l2_note": _Injection(
        "l3_to_l2_note",
        InjectionKind.DIRECTIVE,
        accessor_renderer(lambda b: b.opt_sp.wounds.l3_note, "L3 NOTE TO L2:\n{value}"),
        "Sticky L3→L2 pointer. Mounted only in L2's template; absent from L1.",
        tier=InjectionTier.OPTIONAL,
        char_cap=400,
    ),
    "rendered_prompt": _Injection(
        "rendered_prompt",
        InjectionKind.TRACE,
        accessor_renderer(lambda b: b.opt_sp.render(), "CURRENT PROMPT:\n---\n{value}\n---"),
        "Current best searchpoint's compiled prompt body.",
        tier=InjectionTier.MANDATORY,
        char_cap=2500,
    ),
    "pipeline_param_catalogue": _Injection(
        "pipeline_param_catalogue",
        InjectionKind.DERIVED,
        _r_pipeline_param_catalogue,
        "Pipeline-param menu: name + ≤4-value enum hint per node, plus available models.",
        tier=InjectionTier.MANDATORY,
        char_cap=None,
    ),
    "diagnostics": _Injection(
        "diagnostics",
        InjectionKind.DERIVED,
        _r_diagnostics,
        "Layer-agnostic round readout: STATUS header + RoundDiagnostics body.",
        tier=InjectionTier.CORE,
        char_cap=None,
    ),
    "validation_failures": _Injection(
        "validation_failures",
        InjectionKind.MEASUREMENT,
        _r_validation_failures,
        "Wound 1: L1 parse-time validator failures (per-axis, per-value).",
        tier=InjectionTier.CORE,
        char_cap=None,
    ),
    "runtime_failures": _Injection(
        "runtime_failures",
        InjectionKind.MEASUREMENT,
        _r_runtime_failures,
        "Wound 2: DegradationCheck mid-eval evidence — per-candidate runtime failures.",
        tier=InjectionTier.CORE,
        char_cap=None,
    ),
    "l2_guard_breaches": _Injection(
        "l2_guard_breaches",
        InjectionKind.MEASUREMENT,
        _r_l2_guard_breaches,
        "Wound 4: L2_CONTEXT post-parse guard outcomes; non-empty force-triggers L3 heal.",
        tier=InjectionTier.CORE,
        char_cap=None,
    ),
    "l3_guard_breaches": _Injection(
        "l3_guard_breaches",
        InjectionKind.MEASUREMENT,
        _r_l3_guard_breaches,
        "L3_PLAN post-parse guard outcomes. L3 reads its own past breaches.",
        tier=InjectionTier.CORE,
        char_cap=None,
    ),
    "task_context": _Injection(
        "task_context",
        InjectionKind.TRACE,
        _r_task_context,
        "Persistent task framing dict refined by L2; broadcast to all four prompts.",
        tier=InjectionTier.MANDATORY,
        char_cap=None,  # _r_task_context caps each field at TASK_CONTEXT_VALUE_CAP
    ),
    "critique": _Injection(
        "critique",
        InjectionKind.TRACE,
        _r_critique,
        "Compact view of the most recent L1_CRITIQUE LLM output dict.",
        tier=InjectionTier.MANDATORY,
        char_cap=800,
    ),
    "l1_overrides": _Injection(
        "l1_overrides",
        InjectionKind.TRACE,
        accessor_renderer(
            lambda b: b.opt_sp.l1_overrides, "CURRENT L1 CONFIG: {value}", json_value=True
        ),
        "Current L1 runtime knobs (creativity, n_variants, etc.) as JSON.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "l1_signal_catalogue": _Injection(
        "l1_signal_catalogue",
        InjectionKind.DERIVED,
        _r_l1_signal_catalogue,
        "L1 SIGNAL MENU: sorted L1_POSSIBLE placeholder names L2 may use in l1_layout.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "axis_memory": _Injection(
        "axis_memory",
        InjectionKind.DERIVED,
        _r_axis_memory,
        "Cross-cycle axis-keyed digest from AxisIndex: rankings, persistent failures, "
        "failure clusters, value trends, exhausted axes.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "origin_strengths": _Injection(
        "origin_strengths",
        InjectionKind.MEASUREMENT,
        _r_origin_strengths,
        "Round-0 origin's per-sample hits — the floor variants must preserve.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "intractable_samples": _Injection(
        "intractable_samples",
        InjectionKind.MEASUREMENT,
        _r_intractable_samples,
        "Cumulative cycle-wide miss set — samples no candidate has solved yet this cycle.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "archive_top_runs": _Injection(
        "archive_top_runs",
        InjectionKind.MEASUREMENT,
        _r_archive_top_runs,
        "Top-K historical runs across the dataset's archive — anchor the optimizer "
        "against the best composite ever scored instead of re-discovering it.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "rare_hit_samples": _Injection(
        "rare_hit_samples",
        InjectionKind.MEASUREMENT,
        _r_rare_hit_samples,
        "Samples cracked by ≤3 of ≥10 attempts — names the run(s) that hit them "
        "(recipe pointers). Zero-hit samples surface as capacity-bound.",
        tier=InjectionTier.OPTIONAL,
        char_cap=None,
    ),
    "l1_supplemental_rules": _Injection(
        "l1_supplemental_rules",
        InjectionKind.DIRECTIVE,
        _r_l1_supplemental_rules,
        "Situational rules appended to L1's instruction — auto-triggered from "
        "bundle state (PEAKED axes, runtime failures, chain-bind, continuous-axis, "
        "L2 stall, LaTeX corruption) plus L2-authored entries on opt_sp.",
        tier=InjectionTier.OPTIONAL,
        char_cap=1000,
    ),
    "l1_situational_examples": _Injection(
        "l1_situational_examples",
        InjectionKind.DIRECTIVE,
        _r_l1_situational_examples,
        "Worked examples pinned to currently-active triggers — built-ins shipped "
        "in auto_rules.py plus L2-authored entries on opt_sp. Examples whose "
        "trigger is not active this round are silently filtered.",
        tier=InjectionTier.OPTIONAL,
        char_cap=1000,
    ),
    "prompt_budget_status": _Injection(
        "prompt_budget_status",
        InjectionKind.DERIVED,
        _r_prompt_budget_status,
        "L2's self-heal surface: every per-injection char cap + the actual size "
        "of any block currently over, split by author. l2_context template only.",
        # MANDATORY so the allocator never sheds the block that tells L2 how to
        # heal; tiny + deterministic, so it costs nothing to keep.
        tier=InjectionTier.MANDATORY,
        char_cap=None,
    ),
}


# Every L1-mandatory placeholder MUST be tier MANDATORY — otherwise the budget allocator could
# shed a signal L1 can't run without (parent prompt, plan, framing, mutation surface, critique).
_mistiered_mandatory = sorted(
    name for name in L1_MANDATORY if INJECTIONS[name].tier is not InjectionTier.MANDATORY
)
if _mistiered_mandatory:
    raise RuntimeError(
        f"L1_MANDATORY injections not tier MANDATORY: {_mistiered_mandatory}. "
        "The budget allocator must never be able to shed these."
    )
