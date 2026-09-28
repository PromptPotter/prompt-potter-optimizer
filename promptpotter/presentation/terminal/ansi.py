"""Terminal render target — typed View → ANSI. The markdown / heatmap renderers are the APPLICATION's emit contract and
live in ``promptpotter.application.views.render``; import those from there."""

from __future__ import annotations

from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.view_models import (
    AnyView,
    BenchGradedView,
    CandidatesGeneratedView,
    InitEnterView,
    InitExitView,
    MeasureEnterView,
    OptimizerStepEnterView,
    OptimizerStepExitView,
    RoundCompleteView,
    RoundStartView,
    SpDiffView,
)
from promptpotter.domain.candidate_diff import group_diff_keys
from promptpotter.domain.results import ArmOutcome, scoreboard_rank_key
from promptpotter.presentation.terminal.primitives import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _fmt_delta,
    _node_block,
    _node_line,
    _node_top,
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
        f"  {GREEN}✓{RESET} Initialized  origin={fmt_pct(v.origin_acc)}  "
        f"cycle={v.cycle_id_short}  samples={v.samples}  bench={v.bench_samples} held out  "
        f"obs={obs}"
    ]
    parts: list[str] = []
    if v.task_context_keys:
        parts.append(f"task_context={v.task_context_keys} keys")
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


def _heart_bar(stalls_left: int, cap: int | None) -> str:
    """Banked stalls filled, the rest of the ceiling hollow. The EMPTY pips are the readout: three
    alone cannot distinguish healthy-of-four from nearly-dead-of-seven, and a run banking stalls
    has no ``ROUND n/max`` to carry the scale."""
    if stalls_left <= 0:
        return "💀"
    if cap is None or cap < stalls_left:
        return "♥" * stalls_left
    return "♥" * stalls_left + "♡" * (cap - stalls_left)


def _render_round_start(v: RoundStartView) -> str:
    # A run banking stalls shows the ♥ bank instead of the fixed round ceiling (null/999 when the
    # bank governs the budget); any other run keeps the "ROUND N/max" form.
    standing = v.run_standing
    round_label = (
        f"ROUND {v.round}  {_heart_bar(standing.stalls_left, standing.stalls_left_cap)}"
        if standing is not None and standing.stalls_left is not None
        else f"ROUND {v.round}/{v.max_rounds or 999}"
    )
    arms = "?" if v.arms is None else str(v.arms)
    return "\n".join(
        [
            "",
            _round_rule(round_label, v.standing),
            "",
            _node_block(
                "GENERATE",
                f"Parent accuracy {v.current_acc:.1%}",
                f"Parent prompt   {v.prompt_preview}",
                f"Candidates      {arms}" + (f"   {v.note}" if v.note else ""),
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


def _render_measure_enter(v: MeasureEnterView) -> str:
    return "\n" + _node_top(
        "MEASURE", f"{v.node} · {v.n_candidates} candidates on {v.n_samples} cells"
    )


def _render_round_complete(v: RoundCompleteView) -> str:
    out: list[str] = []
    if len(v.scores) > 3:
        if board := _scoreboard(v.scores, v.winner_label, theta=v.stamps_theta):
            out.append(board)
    elif v.scores:
        parts = [
            f"{s.label}={fmt_pct(s.accuracy)}{f' ({s.outcome})' if s.outcome.cut_short else ''}"
            for s in sorted(
                v.scores,
                key=lambda s: scoreboard_rank_key(
                    s.composite_fitness,
                    s.accuracy,
                    s.theta,
                    is_selected=s.label == v.winner_label,
                    is_partial=s.outcome is ArmOutcome.SKIPPED,
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

    # The campaign says WHICH number headlines this line. `ability` is what a resubset campaign
    # sets (`knobs.py::headline_subset_relative_under_resubset`), because the panel is re-picked
    # each round, so accuracy is subset-relative and a parent that did nothing still moves with it.
    # Accuracy does not disappear; it moves into the parenthetical, so declaring the other loses
    # no reading.
    acc_txt = fmt_pct(v.winner_accuracy)
    ability = v.stamps_theta and v.headline_metric == "ability" and v.ability_theta is not None
    headline = f"θ {v.ability_theta:+.3f}" if ability else acc_txt
    detail = [acc_txt] if ability else []

    if v.improved:
        # An arm that stopped short gets no reference rate rather than the full-set one:
        # subtracting a full panel from a prefix accuracy publishes lift nobody measured.
        detail.append(
            f"vs reference {v.reference_accuracy:.1%}, {_fmt_delta(v.delta)}"
            if v.reference_accuracy is not None and v.delta is not None
            else "no matched reference — stopped before covering its reference's cells"
        )
        sig_tag = f"  {fmt_pvalue(v.p_value)}" if v.p_value is not None else ""
        out.append(
            f"  {GREEN}{BOLD}✓ SELECTED {v.winner_label}{RESET}  {headline}"
            f" ({', '.join(detail)}){comp_tag}{sig_tag}"
        )
    else:
        detail += ["the best-so-far held", f"n={v.winner_total}"]
        out.append(f"  {YELLOW}{BOLD}· HELD{RESET}  {headline} ({', '.join(detail)}){comp_tag}")
    # The selector's own reason, whichever way the round went: the rate on the line above is never
    # what an optimizer's selection read. Its lift interval prints once, in `render_round_stats`.
    if v.verdict_reason:
        out.append(f"  {DIM}why: {v.verdict_reason}{RESET}")

    if not show_inline and v.winner_composite_fitness is not None:
        # No fallback to the cycle's origin composite — the substitution the verdict line refuses.
        for line in render_composite_fitness_block(
            v.winner_composite_fitness,
            v.winner_evaluators,
            formula,
            reference=v.reference_composite,
            use_short_names=bool(v.composite_fitness_formula_short),
        ):
            out.append(f"  {line}")
    return "\n".join(out)


def _render_step_enter(v: OptimizerStepEnterView) -> str:
    return "\n" + _node_block(v.title, *v.lines, label_right=v.tag)


def _render_step_exit(v: OptimizerStepExitView) -> str:
    if not v.headline:
        return ""
    out = [f"  {GREEN}✓{RESET} {v.headline}", *(f"    {line}" for line in v.details)]
    if v.audit is not None:
        # Address the call's canonical home, never re-print it: the audit twin holds it uncapped.
        label, node = v.audit
        out.append(
            f"  {CYAN}{label}{RESET} {DIM}→ .runtime/cache/rounds/round_NNNN.json"
            f"::nodes.{node} (prompt · response · usage){RESET}"
        )
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
        case MeasureEnterView():
            return _render_measure_enter(view)
        case RoundCompleteView():
            return _render_round_complete(view)
        case OptimizerStepEnterView():
            return _render_step_enter(view)
        case OptimizerStepExitView():
            return _render_step_exit(view)
        case BenchGradedView():
            return _render_bench_graded(view)
        case _:
            return ""


def _render_bench_graded(v: BenchGradedView) -> str:
    reading = v.reading
    if reading is None:
        return f"  {YELLOW}bench: no reading — {v.missing}{RESET}"
    composite = reading["composite_fitness"]
    value = "—" if composite is None else f"{composite:.3f}"
    return (
        f"  {DIM}bench R{reading['round']}: composite {value} on "
        f"{reading['n_scored']} held-out rows{RESET}"
    )


_COLLAPSE_WORDS = {
    "no_op_variant": "no-op",
    "duplicate_variant": "duplicate",
    "repeat_variant": "repeat",
}
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

    warning_lines: list[str] = []
    bits = [f"{view.collapses[r]} {w}" for r, w in _COLLAPSE_WORDS.items() if view.collapses.get(r)]
    if bits:
        n_total = sum(1 for label, _ in columns_in if label.startswith("C"))
        n_valid = max(0, n_total - sum(view.collapses.values()))
        cl_text = f" ({', '.join(sorted(clone_labels))})" if clone_labels else ""
        warning_lines.append(
            _node_line(
                f"{YELLOW}⚠ {view.proposer} produced {' / '.join(bits)} variant(s){cl_text} — "
                f"synthetic-zeroed (no API cost). yield={n_valid / n_total:.0%} "
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
