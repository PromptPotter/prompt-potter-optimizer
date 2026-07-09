"""Text render target — typed View → ANSI string for live CLI/notebook.
Per-view renderers + ``to_text`` dispatch. ANSI primitives from ``views.display``;
composite_fitness block from ``shared.composite``."""

from __future__ import annotations

import json

from promptpotter.application.views.view_models import (
    AnyView,
    CandidatesGeneratedView,
    EscalationEnterView,
    EscalationExitView,
    InitEnterView,
    InitExitView,
    L2RefineEnterView,
    L2RefineExitView,
    PlanEnterView,
    PlanExitView,
    ProbeEnterView,
    ProbeExitView,
    RoundCompleteView,
    RoundStartView,
)
from promptpotter.domain.rendering import round_winner_key
from promptpotter.presentation.views.display import (
    BOLD,
    CYAN,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    _node_block,
    _round_rule,
    _scoreboard,
    fmt_pvalue,
)
from promptpotter.presentation.views.render.sp_diff import render_sp_diff
from promptpotter.shared.composite import render_composite_fitness_block


def _render_init_enter(v: InitEnterView) -> str:
    if not v.warnings:
        return ""
    out = [""]
    for w in v.warnings:
        out.append(f"{YELLOW}⚠ {BOLD}{w.title}{RESET}")
        out.append(f"    {YELLOW}{w.detail}{RESET}")
    return "\n".join(out)


def _render_init_exit(v: InitExitView) -> str:
    obs = "ON" if v.obs_on else "OFF"
    out = [
        f"  {GREEN}✓{RESET} Initialized  origin={v.origin_acc:.1%}  "
        f"cycle={v.cycle_id_short}  samples={v.samples}  obs={obs}"
    ]
    parts: list[str] = []
    if v.task_context_keys:
        parts.append(f"task_context={v.task_context_keys} keys")
    if v.l2_round:
        parts.append(f"l2_round={v.l2_round}")
    suffix = f"  ({', '.join(parts)})" if parts else ""
    if v.cached_rounds_count > 0:
        out.append(
            f"    Resuming round {v.resumed_from_round} "
            f"({v.cached_rounds_count} prior rounds cached){suffix}"
        )
    else:
        tail = f"  — starting at round {v.resumed_from_round}{suffix}"
        out.append(f"    Starting fresh (no prior rounds for this cycle){tail}")
    return "\n".join(out)


def _heart_bar(hearts: int, cap: int | None) -> str:
    """``♥♥♥♡♡`` — banked lives filled, the rest of the ceiling hollow; ``💀`` at zero.

    The empty pips ARE the readout: ``♥♥♥`` alone cannot distinguish healthy-of-four from
    nearly-dead-of-seven, and in lives mode there is no ``ROUND n/max`` left to carry the
    scale. Falls back to a bare count when the cap is unknown (lives off, or a dashboard
    written before ``run_limits.lives_cap`` existed)."""
    if hearts <= 0:
        return "💀"
    if cap is None or cap < hearts:
        return "♥" * hearts
    return "♥" * hearts + "♡" * (cap - hearts)


def _render_round_start(v: RoundStartView) -> str:
    if v.has_l1_critique:
        crit = f"from R{v.round - 1}"
    elif v.round <= 1:
        crit = "none yet (first round)"
    else:
        crit = f"none (R{v.round - 1} produced none)"
    # Lives mode → show the ♥ bank instead of the fixed round ceiling (which is null/999
    # when lives governs the budget); non-lives runs keep the "ROUND N/max" form.
    round_label = (
        f"ROUND {v.round}  {_heart_bar(v.hearts, v.hearts_cap)}"
        if v.hearts is not None
        else f"ROUND {v.round}/{v.max_rounds or 999}"
    )
    # `l1_patience` is the distance to the next ESCALATION, not the run's remaining life —
    # hearts own that. Labelling it "patience" beside a ♥ bank put two different facts under
    # one word and read as a duplicate. At 0 the `l1_to_l2` fall-through fires L2 every round,
    # which "stall 1/0" would state as a riddle; say it plainly instead.
    escalation = (
        "L2 every round" if v.patience == 0 else f"stall {v.l1_stall_count}/{v.patience} → L2"
    )
    return "\n".join(
        [
            "",
            _round_rule(
                round_label,
                escalation,
            ),
            "",
            _node_block(
                "GENERATE",
                f"Current best    {v.current_acc:.1%}",
                f"Parent prompt   {v.prompt_preview}",
                f"Candidates      {v.n_variants}   Prior critique: {crit}",
                f"Model           {v.model}",
            ),
        ]
    )


def _render_candidates_generated(v: CandidatesGeneratedView) -> str:
    src = "loaded from disk" if v.source == "disk" else "from LLM"
    return "\n".join(
        [
            f"  {GREEN}✓{RESET} {v.n_candidates} candidates generated ({src})",
            "",
            render_sp_diff(v.sp_diff),
        ]
    )


def _render_round_complete(v: RoundCompleteView) -> str:
    out: list[str] = []
    if len(v.scores) > 3:
        if board := _scoreboard(v.scores, v.winner_label, v.origin_acc):
            out.append(board)
    elif v.scores:
        parts = [
            f"{s.label}={s.accuracy:.1%}{' (aborted)' if s.escalation_aborted else ''}"
            for s in sorted(
                v.scores,
                key=lambda s: round_winner_key(s.composite_fitness, s.accuracy),
                reverse=True,
            )
        ]
        out.append(f"  Scoreboard: {' | '.join(parts)}")

    formula = v.composite_fitness_formula_short or v.composite_fitness_formula
    show_inline = not formula
    comp_tag = (
        f"  composite_fitness={v.winner_composite_fitness:.4f}"
        if show_inline
        and v.winner_composite_fitness is not None
        and v.winner_composite_fitness != v.winner_accuracy
        else ""
    )

    # The verdict line displays the comparison the gate actually used —
    # matched-pair origin (origin restricted to the winner's measured samples).
    # When the winner ran the full set, ``matched_origin_accuracy == origin_acc``
    # and the displayed value is unchanged. PoBB-locked winners get a fair
    # subset origin floor instead of being compared against origin's full 20.
    matched_origin = v.matched_origin_accuracy

    if v.improved:
        sig_tag = f"  {fmt_pvalue(v.p_value)}" if v.p_value is not None else ""
        out.append(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {v.winner_accuracy:.1%}"
            f" (was {matched_origin:.1%}, {_fmt_delta(v.delta)}){comp_tag}{sig_tag}"
            f"  ->  next: {v.next_action}"
        )
    elif v.improved_reason:
        # Positive Δ but blocked by significance or sample-count floor — name
        # the reason so the operator sees why a numerically-better candidate
        # didn't graduate.
        out.append(
            f"  {YELLOW}{BOLD}✗ NOT PROMOTED{RESET}  {v.winner_accuracy:.1%}"
            f" (was {matched_origin:.1%}, {_fmt_delta(v.delta)}, n={v.winner_total})"
            f"  reason: {v.improved_reason}{comp_tag}"
        )
    else:
        out.append(
            f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate "
            f"{v.winner_accuracy:.1%}{comp_tag}"
        )

    if not show_inline and v.winner_composite_fitness is not None:
        # Composite block also compares against matched-pair origin composite
        # so the displayed Δcomposite agrees with the gate.
        origin_composite_for_display = (
            v.matched_origin_composite
            if v.matched_origin_composite is not None
            else v.origin_composite_fitness
        )
        for line in render_composite_fitness_block(
            v.winner_composite_fitness,
            v.winner_evaluators,
            formula,
            origin=origin_composite_for_display,
            use_short_names=bool(v.composite_fitness_formula_short),
        ):
            out.append(f"  {line}")

    if crit := v.l1_critique_text.replace("\n", " ").strip():
        out.append(f"  {CYAN}L1 Critique:{RESET} {crit}")
    return "\n".join(out)


def _render_escalation_enter(v: EscalationEnterView) -> str:
    extras = [f"{wt}: {count} occurrences" for wt, count in v.warning_types.items()]
    return "\n" + _node_block(
        "ESCALATION",
        f"{YELLOW}Degraded: {v.degraded_rate:.0%} of samples{RESET}",
        *extras,
        label_right=f"{v.check_name} → {v.target}",
    )


def _render_escalation_exit(v: EscalationExitView) -> str:
    if not v.classifications:
        return ""
    out = [f"  {CYAN}Warning classifications:{RESET}"]
    out.extend(f"    {wt}: {status}" for wt, status in v.classifications)
    return "\n".join(out)


def _render_l2_refine_enter(v: L2RefineEnterView) -> str:
    if v.l1_overrides:
        items = list(v.l1_overrides.items())
        parts = [f"{k}={s if len(s) <= 30 else s[:27] + '...'}" for k, s in items[:5]]
        extra = len(items) - 5
        body = ", ".join(parts) + (f", +{extra} more" if extra > 0 else "")
        params_line = f"l1_overrides: {body}"
    else:
        params_line = "l1_overrides: (none)"
    return "\n" + _node_block(
        "L2 REFINE CONTEXT",
        f"L1 stalled {v.l1_stall_count} rounds  |  acc={v.current_acc:.1%}  best={v.best_acc:.1%}",
        params_line,
        "LLM analyzing failure patterns...",
        label_right=f"L2 fire {v.l2_round + 1}",
    )


def _render_l2_refine_exit(v: L2RefineExitView) -> str:
    tc = f", {GREEN}task_context updated{RESET}" if v.task_context_changed else ""
    probe = f", {CYAN}action={v.action}{RESET}" if v.action != "continue" else ""
    out = [f"  {GREEN}✓{RESET} L2 decision: {v.param_changes_count} param changes{tc}{probe}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")
    if v.warned_samples:
        out.append(
            f"    {YELLOW}⚠ {v.warned_samples} samples with recurring pipeline warnings{RESET}"
        )

    if v.l2_prompt:
        out.append(f"\n  {CYAN}--- L2 PROMPT (sent to LLM) ---{RESET}")
        out.extend(f"  {CYAN}│{RESET} {line}" for line in v.l2_prompt.split("\n"))
        out.append(f"  {CYAN}--- END PROMPT ---{RESET}")

    if v.l2_response_json is not None:
        out.append(f"\n  {CYAN}--- L2 RESPONSE (raw JSON) ---{RESET}")
        out.extend(
            f"  {CYAN}│{RESET} {line}"
            for line in json.dumps(v.l2_response_json, indent=2).split("\n")[:40]
        )
        out.append(f"  {CYAN}--- END RESPONSE ---{RESET}")
    return "\n".join(out)


def _render_probe_enter(v: ProbeEnterView) -> str:
    extras = [f"  {q[:70]}" for q in v.probe_queries[:5]]
    if len(v.probe_queries) > 5:
        extras.append(f"  ... +{len(v.probe_queries) - 5} more")
    return "\n" + _node_block(
        "PROBE ROUND",
        "Testing warned samples with new settings...",
        *extras,
        label_right=f"{v.n_probe_samples} samples",
    )


def _render_probe_exit(v: ProbeExitView) -> str:
    if not v.n_probed:
        return f"  {YELLOW}⚡ Probe: no matching samples found{RESET}"
    rate = v.probe_hits / v.n_probed
    color = GREEN if rate > 0.5 else YELLOW
    return f"  {color}⚡ Probe: {v.probe_hits}/{v.n_probed} hits ({rate:.0%}){RESET}"


def _render_plan_enter(v: PlanEnterView) -> str:
    plan = v.current_plan_preview
    plan = plan if len(plan) <= 55 else plan[:52] + "..."
    return "\n" + _node_block(
        "L3 MODIFY PLAN",
        f"L2 stalled {v.l2_stall_count} rounds",
        f"Current plan: {plan}",
        "LLM designing new strategy...",
        label_right=f"L3 fire {v.l3_round + 1}",
    )


def _render_plan_exit(v: PlanExitView) -> str:
    plan = v.new_plan_preview
    plan = plan if len(plan) <= 55 else plan[:52] + "..."
    out = [f"  {GREEN}✓{RESET} New plan: {plan}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")
    return "\n".join(out)


def to_text(view: AnyView) -> str:
    """Dispatch a typed view to its ANSI text renderer. Explicit match so each
    ``grep _render_*`` lands on the call site and mypy narrows the view type per arm."""
    match view:
        case InitEnterView():
            return _render_init_enter(view)
        case InitExitView():
            return _render_init_exit(view)
        case RoundStartView():
            return _render_round_start(view)
        case CandidatesGeneratedView():
            return _render_candidates_generated(view)
        case RoundCompleteView():
            return _render_round_complete(view)
        case EscalationEnterView():
            return _render_escalation_enter(view)
        case EscalationExitView():
            return _render_escalation_exit(view)
        case L2RefineEnterView():
            return _render_l2_refine_enter(view)
        case L2RefineExitView():
            return _render_l2_refine_exit(view)
        case ProbeEnterView():
            return _render_probe_enter(view)
        case ProbeExitView():
            return _render_probe_exit(view)
        case PlanEnterView():
            return _render_plan_enter(view)
        case PlanExitView():
            return _render_plan_exit(view)
        case _:
            return ""


__all__ = ["to_text"]
