from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from promptpotter.application.views.render.optimizer_prompt_text import fmt_pct
from promptpotter.application.views.render.primitives import (
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
    fmt_coverage,
)
from promptpotter.domain.bench import BENCH_STATE_INFO
from promptpotter.domain.candidate_diff import group_diff_keys
from promptpotter.domain.phase_views import (
    BenchEnterView,
    BenchGradedView,
    BenchScoredView,
    CandidatesGeneratedView,
    InitEnterView,
    InitExitView,
    MeasureEnterView,
    OptimizerStepEnterView,
    OptimizerStepExitView,
    PhaseView,
    RoundCompleteView,
    RoundStartView,
    RunSpendView,
    SpDiffView,
    VerifyEnterView,
    VerifyGradedView,
)
from promptpotter.domain.ruler import THETA_CAVEAT_INFO, ThetaCaveat, is_flat_ruler_id
from promptpotter.domain.spend import CEILING_METER_LABELS, RATE_PRICED_LABEL
from promptpotter.domain.wounds import COLLAPSE_WORDS
from promptpotter.shared.composite import render_composite_fitness_block

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.ruler import AbilityReading


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


def _render_round_start(v: RoundStartView) -> str:
    standing = v.run_standing
    round_label = (
        f"ROUND {v.round}  {standing.lives.bar}"
        if standing is not None and standing.lives is not None
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
                f"Parent accuracy {fmt_pct(v.parent_accuracy)}",
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


def render_round_verdict(
    v: RoundCompleteView, basis: Sequence[str], ability: AbilityReading | None
) -> str:
    out: list[str] = [""]
    if board := _scoreboard(v.arms):
        out.append(board)

    formula = v.composite_fitness_formula_short or v.composite_fitness_formula
    show_inline = not formula
    comp_tag = (
        f"  composite_fitness={v.composite_fitness:.4f}"
        if show_inline and v.composite_fitness is not None and v.composite_fitness != v.accuracy
        else ""
    )

    acc_txt = fmt_pct(v.accuracy)
    # A cold θ is logit-accuracy on the arm's own subset, so the headline stays accuracy.
    theta = (
        ability.theta
        if v.display_metric == "ability"
        and ability is not None
        and not is_flat_ruler_id(ability.ruler_id or "")
        else None
    )
    headline = acc_txt if theta is None else f"θ {theta:+.3f}"
    detail = [] if theta is None else [acc_txt]

    selected = next((s.vs_reference for s in v.arms if s.election.selected), None)
    if v.improved:
        # An arm that stopped short gets no reference rate: over a prefix it is a lift nobody measured.
        lift = selected.on_whole_set if selected else None
        detail.append(
            f"vs reference {lift.rate_a:.1%}, {_fmt_delta(lift.estimate.value)}"
            if lift is not None
            else "no matched reference — stopped before covering its reference's cells"
        )
        out.append(
            f"  {GREEN}{BOLD}✓ SELECTED {v.ended_on}{RESET}  {headline}"
            f" ({', '.join(detail)}){comp_tag}"
        )
    else:
        detail += ["the best-so-far held", f"n={v.total}"]
        out.append(f"  {YELLOW}{BOLD}· HELD{RESET}  {headline} ({', '.join(detail)}){comp_tag}")
    if v.verdict_reason:
        out.append(f"  {DIM}why: {v.verdict_reason}{RESET}")
    # Every number above still renders under a SILENT caveat: this line is the terminal's only notice.
    caveats: dict[ThetaCaveat, list[str]] = {}
    if ability is not None and ability.caveat is not None:
        caveats[ability.caveat] = []
    for arm in v.arms:
        if arm.ability is not None and arm.ability.caveat is not None:
            caveats.setdefault(arm.ability.caveat, []).append(arm.arm.label)
    for caveat, labels in caveats.items():
        scope = f" ({', '.join(labels)})" if labels else ""
        out.append(
            f"  {YELLOW}⚠ θ caveat{scope}: {THETA_CAVEAT_INFO[caveat].head}{RESET}"
            f" {DIM}[{caveat.value}]{RESET}"
        )
    out.extend(f"  {line}" for line in basis)

    if not show_inline and v.composite_fitness is not None:
        for line in render_composite_fitness_block(
            v.composite_fitness,
            v.evaluators,
            formula,
            reference=selected.reference_level("objective") if selected else None,
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
        label, node = v.audit
        out.append(
            f"  {CYAN}{label}{RESET} {DIM}→ .runtime/cache/rounds/round_NNNN.json"
            f"::nodes.{node} (prompt · response · usage){RESET}"
        )
    return "\n".join(out)


def to_text(view: PhaseView) -> str:
    """``RoundCompleteView`` renders nothing here: the readout holds it for ``render_round_verdict``."""
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
        case OptimizerStepEnterView():
            return _render_step_enter(view)
        case OptimizerStepExitView():
            return _render_step_exit(view)
        case BenchEnterView():
            return _render_bench_enter(view)
        case BenchGradedView():
            return _render_bench_graded(view)
        case BenchScoredView():
            return _render_bench_scored(view)
        case VerifyEnterView():
            return f"  {DIM}verify {view.label}: {view.rows} unseen cells, {view.strategy}{RESET}"
        case VerifyGradedView():
            return _render_verify_graded(view)
        case RoundCompleteView():
            return ""
        case _:
            assert_never(view)


def render_run_spend(v: RunSpendView) -> str:
    bits = [
        f"billed ${v.billed_usd:.4f} (what the provider reported it charged)",
        f"{RATE_PRICED_LABEL} ${v.rate_priced_usd:.4f} (calls no provider reported a charge for)",
        f"incurred ${v.incurred_usd:.4f} (every cell priced, replays included)",
    ]
    counted = CEILING_METER_LABELS[v.meter]
    if v.usd_cap is not None:
        bits.append(f"cap ${v.metered_usd:.4f} of ${v.usd_cap:.2f} {counted}")
    if v.token_cap is not None:
        bits.append(f"cap {v.metered_tokens:,} of {v.token_cap:,} tokens {counted}")
    return f"  {DIM}spend: {' · '.join(bits)}{RESET}"


def _render_bench_enter(v: BenchEnterView) -> str:
    subject = "the origin" if v.subject == "origin" else f"the R{v.round} selection"
    return f"  {DIM}bench pass: {subject} on {v.rows} held-out rows{RESET}"


def _render_bench_graded(v: BenchGradedView) -> str:
    reading = v.reading
    if reading is None:
        cut = v.bench_pass.stop
        stop = "" if cut is None else f": {cut.cause.value}"
        return f"  {YELLOW}bench: no reading — {BENCH_STATE_INFO[v.state].label}{stop}{RESET}"
    level = reading.level
    value = "—" if level is None else f"{level.value:.3f}"
    return (
        f"  {DIM}bench R{reading.round}: {reading.headline} {value} on "
        f"{reading.n} held-out rows{RESET}"
    )


def _render_verify_graded(v: VerifyGradedView) -> str:
    r = v.reading
    levels = " → ".join(
        "—" if (level := side.accuracy) is None else f"{level.value:.3f}"
        for side in (r.recorded, r.fresh)
    )
    verdict = {True: " — held", False: f" — {YELLOW}dropped{RESET}{DIM}", None: ""}[r.held]
    lift = ""
    pair = r.vs_origin
    if pair.headline is not None and pair.coverage is not None:
        paired = pair.headline.estimate
        lift = (
            f", lift over C0 {paired.value:+.3f} "
            f"[{paired.ci_lo:+.3f}, {paired.ci_hi:+.3f}] on {fmt_coverage(pair.coverage)}"
        )
    return (
        f"  {DIM}verify {r.label}: accuracy {levels} on {r.fresh.n} unseen cells"
        f"{lift}{verdict}{RESET}"
    )


def _render_bench_scored(v: BenchScoredView) -> str:
    bench = v.bench
    column = bench.headline
    graded = bench.graded
    if graded is None:
        # Loud only where a pass or the run broke; a bench still waiting is no fault.
        ink = YELLOW if BENCH_STATE_INFO[bench.status.state].fault else DIM
        return f"  {ink}bench: no headline — {bench.status.sentence}{RESET}"
    selected = graded.selected
    levels = " → ".join(
        "—" if (level := reading.of(column)) is None else f"{level.value:.3f}" for reading in graded
    )
    band = ""
    if (lift := bench.lift(column)) is not None:
        est = lift.estimate
        band = f", lift {est.value:+.3f} [{est.ci_lo:+.3f}, {est.ci_hi:+.3f}]"
    return (
        f"  bench: {column} {levels} (origin → R{selected.round} selection, "
        f"{selected.n} held-out rows{band})"
    )


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
    bits = [f"{view.collapses[r]} {w}" for r, w in COLLAPSE_WORDS.items() if view.collapses.get(r)]
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
    # Keyed by VALUE, so one prompt shared by Start/Parent/candidates stays a single legend row.
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
        if len(rendered_groups) > 1:
            sep_name = node_name or "prompt"
            sep = f"{'─── ' + sep_name + ' ':─<{max_key + 2}}"
            out.append(_node_line(f"{DIM}{sep}{RESET}"))
        for k, cells in rows:
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w[ci]}}" for ci, c in enumerate(cells))
            out.append(_node_line(row))

    if legend:
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


__all__ = ["render_round_verdict", "render_run_spend", "render_sp_diff", "to_text"]
