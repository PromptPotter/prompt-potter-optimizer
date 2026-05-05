"""Text render target — typed View → ANSI string for live CLI.

Pure dispatch function: ``to_text(view)`` returns the formatted string for
each view type. ANSI primitives (``_node_block``, ``_scoreboard``,
``fmt_pvalue``, …) come from ``display.py``; the per-candidate composite_fitness
block from ``shared/composite.py``. No I/O, no global state.

The ``_render_l1_generate_exit`` path uses an internal ``render_sp_diff``
helper that turns the candidate flat-dict diff into an N-column table.
"""

from __future__ import annotations

import json

from promptpotter.domain.opt_search_point import group_diff_keys
from promptpotter.presentation.views.display import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    _node_block,
    _node_line,
    _round_rule,
    _scoreboard,
    fmt_pvalue,
)
from promptpotter.presentation.views.view_models import (
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
    SpDiffView,
)
from promptpotter.shared.composite import (
    render_composite_fitness_block,
)

__all__ = ["to_text"]


# --- SearchPoint diff renderer (internal helper) --------------------------

_SP_DIFF_ABSENT = "-"
_SP_DIFF_UNCHANGED = "·"
_SP_DIFF_VAL_INLINE_MAX = 12


def _render_sp_diff(view: SpDiffView) -> str:
    columns_in = list(view.columns)
    if len(columns_in) < 2:
        return ""

    clone_labels = set(view.clone_labels)
    columns: list[tuple[str, dict[str, str]]] = [
        (
            f"{label}[clone]" if label in clone_labels else label,
            flat,
        )
        for label, flat in columns_in
    ]
    node_param_keys = view.node_param_keys
    round_num = view.round_num
    n_no_op = view.l1_n_no_op
    n_duplicate = view.l1_n_duplicate
    l1_yield = view.l1_yield

    warning_lines: list[str] = []
    if n_no_op or n_duplicate:
        n_total = sum(1 for label, _ in columns_in if label.startswith("C"))
        n_valid = max(0, n_total - n_no_op - n_duplicate)
        bits: list[str] = []
        if n_no_op:
            bits.append(f"{n_no_op} no-op")
        if n_duplicate:
            bits.append(f"{n_duplicate} duplicate")
        bits_text = " / ".join(bits)
        cl_text = f" ({', '.join(sorted(clone_labels))})" if clone_labels else ""
        warning_lines.append(
            _node_line(
                f"{YELLOW}⚠ L1 produced {bits_text} variant(s){cl_text} — "
                f"synthetic-zeroed (no API cost). yield={l1_yield:.0%} "
                f"({n_valid}/{n_total} valid).{RESET}"
            )
        )

    all_keys = {k for _, d in columns for k in d}
    diff_keys = sorted(k for k in all_keys if len({d.get(k) for _, d in columns}) > 1)
    if not diff_keys:
        return "\n".join(warning_lines) if warning_lines else ""

    lookup: dict[str, str] = {}
    legend: list[tuple[str, str]] = []
    code_idx = 0

    def _get_code(val: str) -> str:
        nonlocal code_idx
        if val in lookup:
            return lookup[val]
        code = chr(ord("a") + code_idx)
        code_idx += 1
        lookup[val] = f"[{code}]"
        legend.append((f"[{code}]", val))
        return f"[{code}]"

    def _cell(val: str | None, prior: str | None) -> str:
        if val is None:
            return _SP_DIFF_ABSENT
        if val == prior:
            return _SP_DIFF_UNCHANGED
        if len(val) <= _SP_DIFF_VAL_INLINE_MAX:
            return val
        return _get_code(val)

    max_key = max(len(k) for k in diff_keys)

    groups = group_diff_keys(diff_keys, node_param_keys)
    rendered_groups: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for node_name, group_keys in groups:
        rows: list[tuple[str, list[str]]] = []
        for k in group_keys:
            cells: list[str] = []
            prev_val: str | None = None
            for _, d in columns:
                v = d.get(k)
                cells.append(_cell(v, prev_val))
                prev_val = v
            rows.append((k, cells))
        rendered_groups.append((node_name, rows))

    n_cols = len(columns)
    col_w: list[int] = []
    for ci in range(n_cols):
        label_w = len(columns[ci][0])
        cell_w = max(
            (len(cells[ci]) for _, rows in rendered_groups for _, cells in rows),
            default=0,
        )
        col_w.append(max(label_w, cell_w) + 2)

    out: list[str] = list(warning_lines)
    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    out.append(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(
        f"{label:<{col_w[ci]}}" for ci, (label, _) in enumerate(columns)
    )
    out.append(_node_line(hdr))

    for node_name, rows in rendered_groups:
        if node_name and len(rendered_groups) > 1:
            sep = f"{'─── ' + node_name + ' ':─<{max_key + 2}}"
            out.append(_node_line(f"{DIM}{sep}{RESET}"))
        for k, cells in rows:
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w[ci]}}" for ci, c in enumerate(cells))
            out.append(_node_line(row))

    if legend:
        out.append(_node_line(""))
        out.append(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            out.append(_node_line(f"  {code} {full}"))

    return "\n".join(out)


# --- per-view renderers ---------------------------------------------------


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
        f"  {GREEN}✓{RESET} Initialized  baseline={v.baseline_acc:.1%}  "
        f"cycle={v.cycle_id_short}  samples={v.samples}  obs={obs}"
    ]
    if v.bootstrap_critique:
        out.append(f"    {CYAN}Bootstrap L1 critique:{RESET} {v.bootstrap_critique}")
    if v.resumed_from_round:
        parts: list[str] = []
        if v.l1_critique_chars:
            parts.append(f"l1_critique={v.l1_critique_chars} chars")
        if v.task_context_keys:
            parts.append(f"task_context={v.task_context_keys} keys")
        if v.l2_round:
            parts.append(f"l2_round={v.l2_round}")
        suffix = f"  ({', '.join(parts)})" if parts else ""
        out.append(
            f"    Resumed from round {v.resumed_from_round} "
            f"({v.resumed_from_round} rounds cached){suffix}"
        )
    else:
        out.append("    Starting fresh (no prior rounds for this cycle)")
    return "\n".join(out)


def _render_round_start(v: RoundStartView) -> str:
    crit = "YES" if v.has_l1_critique else "NO"
    return "\n".join(
        [
            "",
            _round_rule(
                f"ROUND {v.round}/{v.max_rounds or 999}",
                f"patience {v.l1_stall_count}/{v.patience}",
            ),
            "",
            _node_block(
                "GENERATE",
                f"Current best    {v.current_acc:.1%}",
                f"Prompt          {v.prompt_preview}",
                f"Candidates      {v.n_variants}   Creativity: {v.creativity}   Critique: {crit}",
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
            _render_sp_diff(v.sp_diff),
        ]
    )


def _render_round_complete(v: RoundCompleteView) -> str:
    out: list[str] = []
    score_dicts = [
        {
            "label": s.label,
            "accuracy": s.accuracy,
            "composite_fitness": s.composite_fitness,
            "hits": s.hits,
            "total": s.total,
            "escalation_aborted": s.escalation_aborted,
        }
        for s in v.scores
    ]
    if len(score_dicts) > 3:
        if board := _scoreboard(score_dicts, v.winner_label, v.baseline_acc):
            out.append(board)
    elif score_dicts:
        parts = [
            f"{s['label']}={s['accuracy']:.1%}{' (aborted)' if s['escalation_aborted'] else ''}"
            for s in sorted(
                score_dicts,
                key=lambda s: (s.get("composite_fitness") or s["accuracy"], s["accuracy"]),
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

    if v.improved:
        sig_tag = f"  {fmt_pvalue(v.p_value)}" if v.p_value is not None else ""
        out.append(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {v.winner_accuracy:.1%}"
            f" (was {v.baseline_acc:.1%}, {_fmt_delta(v.delta)}){comp_tag}{sig_tag}"
            f"  ->  next: {v.next_action}"
        )
    else:
        out.append(
            f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate "
            f"{v.winner_accuracy:.1%}{comp_tag}"
        )

    if not show_inline and v.winner_composite_fitness is not None:
        for line in render_composite_fitness_block(
            v.winner_composite_fitness,
            v.winner_evaluators,
            formula,
            baseline=v.baseline_composite_fitness,
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
        f"{YELLOW}Degraded: {v.degraded_rate:.0%} of queries{RESET}",
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
    if v.current_params:
        items = list(v.current_params.items())
        parts = [f"{k}={s if len(s) <= 30 else s[:27] + '...'}" for k, s in items[:5]]
        extra = len(items) - 5
        body = ", ".join(parts) + (f", +{extra} more" if extra > 0 else "")
        params_line = f"Current params: {body}"
    else:
        params_line = "Current params: (none)"
    return "\n" + _node_block(
        "L2 REFINE CONTEXT",
        f"L1 stalled {v.l1_stall_count} rounds  |  acc={v.current_acc:.1%}  best={v.best_acc:.1%}",
        params_line,
        "LLM analyzing failure patterns...",
        label_right=f"L2 round {v.l2_round}",
    )


def _render_l2_refine_exit(v: L2RefineExitView) -> str:
    tc = f", {GREEN}task_context updated{RESET}" if v.task_context_changed else ""
    probe = f", {CYAN}action={v.action}{RESET}" if v.action != "continue" else ""
    out = [f"  {GREEN}✓{RESET} L2 decision: {v.param_changes_count} param changes{tc}{probe}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")
    if v.warned_queries:
        out.append(
            f"    {YELLOW}⚠ {v.warned_queries} queries with recurring "
            f"pipeline warnings ({v.top_warning}){RESET}"
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
        "Testing warned queries with new settings...",
        *extras,
        label_right=f"{v.n_probe_queries} queries",
    )


def _render_probe_exit(v: ProbeExitView) -> str:
    if not v.n_probed:
        return f"  {YELLOW}⚡ Probe: no matching queries found{RESET}"
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
        label_right=f"L3 round {v.l3_round}",
    )


def _render_plan_exit(v: PlanExitView) -> str:
    plan = v.new_plan_preview
    plan = plan if len(plan) <= 55 else plan[:52] + "..."
    out = [f"  {GREEN}✓{RESET} New plan: {plan}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")
    return "\n".join(out)


def to_text(view: AnyView) -> str:
    """Dispatch a typed view to its ANSI text renderer."""
    if isinstance(view, InitEnterView):
        return _render_init_enter(view)
    if isinstance(view, InitExitView):
        return _render_init_exit(view)
    if isinstance(view, RoundStartView):
        return _render_round_start(view)
    if isinstance(view, CandidatesGeneratedView):
        return _render_candidates_generated(view)
    if isinstance(view, RoundCompleteView):
        return _render_round_complete(view)
    if isinstance(view, EscalationEnterView):
        return _render_escalation_enter(view)
    if isinstance(view, EscalationExitView):
        return _render_escalation_exit(view)
    if isinstance(view, L2RefineEnterView):
        return _render_l2_refine_enter(view)
    if isinstance(view, L2RefineExitView):
        return _render_l2_refine_exit(view)
    if isinstance(view, ProbeEnterView):
        return _render_probe_enter(view)
    if isinstance(view, ProbeExitView):
        return _render_probe_exit(view)
    if isinstance(view, PlanEnterView):
        return _render_plan_enter(view)
    if isinstance(view, PlanExitView):
        return _render_plan_exit(view)
    return ""
