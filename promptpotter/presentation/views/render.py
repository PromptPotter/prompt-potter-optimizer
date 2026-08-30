"""Terminal render target — typed View → ANSI. The markdown / heatmap / sweep-summary renderers are the APPLICATION's emit contract and
live in ``promptpotter.application.views.render``; import those from there."""

from __future__ import annotations

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
    RoundCompleteView,
    RoundStartView,
    SpDiffView,
)
from promptpotter.domain.candidate_diff import group_diff_keys
from promptpotter.domain.rendering import display_rank_key
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
    """Banked lives filled, the rest of the ceiling hollow. The EMPTY pips are the readout: three hearts alone cannot
    distinguish healthy-of-four from nearly-dead-of-seven, and lives mode has no ``ROUND n/max`` to carry the scale."""
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
                f"Parent accuracy {v.current_acc:.1%}",
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
        if board := _scoreboard(v.scores, v.winner_label):
            out.append(board)
    elif v.scores:
        parts = [
            f"{s.label}={s.accuracy:.1%}{' (aborted)' if s.escalation_aborted else ''}"
            for s in sorted(
                v.scores,
                key=lambda s: display_rank_key(
                    s.composite_fitness,
                    s.accuracy,
                    s.theta,
                    is_winner=s.label == v.winner_label,
                    is_partial=bool(s.partial_reason),
                ),
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

    # A winner that stopped short gets no "(was …)" clause rather than the full-set rate:
    # subtracting a full panel from a prefix accuracy publishes lift nobody measured.
    versus = (
        f"was {v.matched_parent_accuracy:.1%}, {_fmt_delta(v.delta)}"
        if v.matched_parent_accuracy is not None and v.delta is not None
        else "no matched parent — winner stopped before covering the panel"
    )

    # The campaign says WHICH number headlines this line. `ability` is what a resubset campaign
    # sets (`knobs.py::headline_subset_relative_under_resubset`), because the panel is re-picked
    # each round, so accuracy is subset-relative and a parent that did nothing still moves with it.
    # Accuracy does not disappear; it moves into the parenthetical, so declaring the other loses
    # no reading.
    if v.headline_metric == "ability" and v.ability_theta is not None:
        headline = f"θ {v.ability_theta:+.3f}"
        detail = f"{v.winner_accuracy:.1%}, {versus}"
    else:
        headline = f"{v.winner_accuracy:.1%}"
        detail = versus

    if v.improved:
        sig_tag = f"  {fmt_pvalue(v.p_value)}" if v.p_value is not None else ""
        out.append(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {headline}"
            f" ({detail}){comp_tag}{sig_tag}"
            f"  ->  next: {v.next_action}"
        )
    else:
        out.append(
            f"  {YELLOW}{BOLD}✗ NOT PROMOTED{RESET}  {headline}"
            f" ({detail}, n={v.winner_total}){comp_tag}"
        )
    # The round is won on θ-lift, so the accuracy on the line above is never the number that
    # decided it. The reason prints whichever way the round went — on a win as much as a hold, or
    # "why did THIS one win?" is answered nowhere. The lift interval is NOT repeated here;
    # `live/phase.py::render_round_stats` prints it once at round close.
    if v.verdict_reason:
        out.append(f"  {DIM}why: {v.verdict_reason}{RESET}")

    if not show_inline and v.winner_composite_fitness is not None:
        # No fallback to the cycle's origin composite — the substitution `versus` above refuses.
        for line in render_composite_fitness_block(
            v.winner_composite_fitness,
            v.winner_evaluators,
            formula,
            parent=v.matched_parent_composite,
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
    # The two L1 surfaces an L2 fire can touch — the same pair `l2_targets_l1_surface`
    # scores it on, so what the operator reads matches what the validator judges. A fire
    # showing neither is the wasted escalation that check exists to catch.
    layout = f", {GREEN}l1_layout edited{RESET}" if v.l1_layout_changed else ""
    axis = f", {CYAN}axis={v.axis_targeted}{RESET}" if v.axis_targeted else ""
    out = [f"  {GREEN}✓{RESET} L2 decision: {v.param_changes_count} param changes{layout}{axis}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")

    # Address the I/O, never re-print it — and address its CANONICAL home. The audit twin
    # assembles the whole call human-readably and uncapped; this record carries no copy of it,
    # so a dump here had nothing local to quote and the old `[:40]` on the response amputated
    # what it did quote. `AuditTrailView` owns deep LLM I/O; this line points at it.
    out.append(
        f"  {CYAN}L2 call{RESET} {DIM}→ .runtime/cache/rounds/round_NNNN.json"
        f"::nodes.l2_context (prompt · response · usage){RESET}"
    )
    return "\n".join(out)


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
        case PlanEnterView():
            return _render_plan_enter(view)
        case PlanExitView():
            return _render_plan_exit(view)
        case _:
            return ""


_SP_DIFF_ABSENT = "-"
_SP_DIFF_UNCHANGED = "·"
_SP_DIFF_VAL_INLINE_MAX = 12


def render_sp_diff(view: SpDiffView) -> str:
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
    n_repeat = view.l1_n_repeat
    l1_yield = view.l1_yield

    warning_lines: list[str] = []
    if n_no_op or n_duplicate or n_repeat:
        n_total = sum(1 for label, _ in columns_in if label.startswith("C"))
        n_valid = max(0, n_total - n_no_op - n_duplicate - n_repeat)
        bits: list[str] = []
        if n_no_op:
            bits.append(f"{n_no_op} no-op")
        if n_duplicate:
            bits.append(f"{n_duplicate} duplicate")
        if n_repeat:
            bits.append(f"{n_repeat} repeat")
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
    # code → (byte length, the flat keys it appears under, the column labels carrying it). Keyed
    # by VALUE like the codes are, so one origin prompt shared by Start/Parent/candidates stays a
    # single row instead of one per column.
    legend: dict[str, tuple[int, set[str], list[str]]] = {}
    code_idx = 0

    def _get_code(val: str, key: str, column: str) -> str:
        nonlocal code_idx
        code = lookup.get(val)
        if code is None:
            code = f"[{chr(ord('a') + code_idx)}]"
            code_idx += 1
            lookup[val] = code
            legend[code] = (len(val), set(), [])
        _, keys, cols = legend[code]
        keys.add(key)
        if column not in cols:
            cols.append(column)
        return code

    def _cell(val: str | None, prior: str | None, key: str, column: str) -> str:
        if val is None:
            return _SP_DIFF_ABSENT
        if val == prior:
            return _SP_DIFF_UNCHANGED
        if len(val) <= _SP_DIFF_VAL_INLINE_MAX:
            return val
        return _get_code(val, key, column)

    max_key = max(len(k) for k in diff_keys)

    groups = group_diff_keys(diff_keys, node_param_keys)
    rendered_groups: list[tuple[str, list[tuple[str, list[str]]]]] = []
    for node_name, group_keys in groups:
        rows: list[tuple[str, list[str]]] = []
        for k in group_keys:
            cells: list[str] = []
            start_val = columns[0][1].get(k) if columns else None
            parent_val = columns[1][1].get(k) if len(columns) > 1 else None
            for ci, (_, d) in enumerate(columns):
                v = d.get(k)
                if ci == 0:
                    prior: str | None = None
                elif ci == 1:
                    prior = start_val
                else:
                    prior = parent_val
                cells.append(_cell(v, prior, k, columns[ci][0]))
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
        # Prompt-field rows carry node_name "" (group_diff_keys' catch-all); label
        # them "prompt" so a prompt mutation reads as one in the live diff instead
        # of as unlabeled rows mixed with node.param tweaks. The Values: legend below
        # sizes and addresses each elided value; the text itself is in the round file.
        if len(rendered_groups) > 1:
            sep_name = node_name or "prompt"
            sep = f"{'─── ' + sep_name + ' ':─<{max_key + 2}}"
            out.append(_node_line(f"{DIM}{sep}{RESET}"))
        for k, cells in rows:
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w[ci]}}" for ci, c in enumerate(cells))
            out.append(_node_line(row))

    if legend:
        # Address each value, never re-print it. `candidate_scores[].prompt_fields` already holds
        # the full text in queryable form, and re-dumping it here cost the Start and Parent columns
        # once per round for the life of the run — while the [a]/[b] indirection stripped the very
        # key→value association the JSON keeps. So: what changed, how big, and on which columns.
        rf = f"round_{round_num:04d}.json" if round_num is not None else "the round file"
        out.append(_node_line(""))
        out.append(
            _node_line(
                f"{CYAN}Values{RESET} {DIM}— full text in {rf}"
                f"::candidate_scores[].prompt_fields / .pipeline_params, joined on label{RESET}"
            )
        )
        key_w = max(len(" ".join(sorted(keys))) for _, keys, _ in legend.values())
        for code, (n_bytes, keys, cols) in legend.items():
            names = " ".join(sorted(keys))
            out.append(
                _node_line(
                    f"  {code} {names:<{key_w}}  {n_bytes:>7,} B  {DIM}{' '.join(cols)}{RESET}"
                )
            )

    return "\n".join(out)


__all__ = ["render_sp_diff", "to_text"]
