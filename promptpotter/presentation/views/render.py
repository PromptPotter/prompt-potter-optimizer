"""Render targets — typed View → ANSI text or markdown.

Two stateless dispatch entry points:

- ``to_text(view)`` — the live CLI/notebook path. ANSI primitives
  (``_node_block``, ``_scoreboard``, ``fmt_pvalue``, …) come from
  ``display.py``; the per-candidate composite_fitness block from
  ``shared/composite.py``.
- ``to_markdown(LogMdView)`` — the per-cycle digest the runner writes
  after each round into ``campaigns/<cycle_id>/log.md``.

Plus ``render_hard_sample_heatmap`` (plain-text heatmap, exposed for
the standalone CLI hard-sample run) and ``render_sweep_summary`` (sweep
batch markdown). No I/O, no global state.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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
    ForkSummaryView,
    HardSamplesView,
    InitEnterView,
    InitExitView,
    L2RefineEnterView,
    L2RefineExitView,
    LogMdView,
    PlanEnterView,
    PlanExitView,
    ProbeEnterView,
    ProbeExitView,
    RoundCompleteView,
    RoundDigestView,
    RoundStartView,
    SpDiffView,
    SweepSummaryView,
)
from promptpotter.shared.composite import render_composite_fitness_block

__all__ = ["render_hard_sample_heatmap", "render_sweep_summary", "to_markdown", "to_text"]


# ===========================================================================
# Text render target — typed View → ANSI string for live CLI/notebook.
# ===========================================================================


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


# --- per-view text renderers ---------------------------------------------


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
    if v.resumed_from_round:
        parts: list[str] = []
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
        label_right=f"L2 round {v.l2_round}",
    )


def _render_l2_refine_exit(v: L2RefineExitView) -> str:
    tc = f", {GREEN}task_context updated{RESET}" if v.task_context_changed else ""
    probe = f", {CYAN}action={v.action}{RESET}" if v.action != "continue" else ""
    out = [f"  {GREEN}✓{RESET} L2 decision: {v.param_changes_count} param changes{tc}{probe}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")
    if v.warned_samples:
        out.append(
            f"    {YELLOW}⚠ {v.warned_samples} samples with recurring "
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
        label_right=f"L3 round {v.l3_round}",
    )


def _render_plan_exit(v: PlanExitView) -> str:
    plan = v.new_plan_preview
    plan = plan if len(plan) <= 55 else plan[:52] + "..."
    out = [f"  {GREEN}✓{RESET} New plan: {plan}"]
    if v.changes_description:
        out.append(f"    {v.changes_description}")
    return "\n".join(out)


_TEXT_RENDERERS: dict[type, Callable[..., str]] = {
    InitEnterView: _render_init_enter,
    InitExitView: _render_init_exit,
    RoundStartView: _render_round_start,
    CandidatesGeneratedView: _render_candidates_generated,
    RoundCompleteView: _render_round_complete,
    EscalationEnterView: _render_escalation_enter,
    EscalationExitView: _render_escalation_exit,
    L2RefineEnterView: _render_l2_refine_enter,
    L2RefineExitView: _render_l2_refine_exit,
    ProbeEnterView: _render_probe_enter,
    ProbeExitView: _render_probe_exit,
    PlanEnterView: _render_plan_enter,
    PlanExitView: _render_plan_exit,
}


def to_text(view: AnyView) -> str:
    """Dispatch a typed view to its ANSI text renderer."""
    fn = _TEXT_RENDERERS.get(type(view))
    return fn(view) if fn else ""


# ===========================================================================
# Markdown render target — typed View → markdown string for log.md.
# ===========================================================================


def render_sweep_summary(view: SweepSummaryView) -> str:
    """Markdown summary for a sweep batch dir — header + payload table."""
    lines = [
        f"# Sweep batch {view.batch_id}",
        "",
        f"- Parent cycle: `{view.parent_cycle_id}`",
        f"- Family root: `{view.family_root}`",
        f"- Started: {view.started_at}",
        f"- Completed: {view.completed_at}",
        f"- Forks minted: {view.n_minted} of {view.n_payloads}",
        "",
        "## Payloads",
        "",
        "| Source | Status | Cycle |",
        "|---|---|---|",
    ]
    for row in view.payloads:
        lines.append(f"| `{row.source_file}` | {row.status} | `{row.cycle_id}` |")
    return "\n".join(lines) + "\n"


# --- hard-sample-sorter heatmap ------------------------------------------

_HIT = "█"
_MISS = "▒"
_UNMEASURED = "·"
_HEATMAP_LABEL_W = 10
_HEATMAP_CELL_W = 2


def _cell_index(artifact: dict) -> dict[tuple[str, int], bool]:
    return {(c["c"], int(c["s"])): bool(c["hit"]) for c in artifact.get("cells", [])}


def render_hard_sample_heatmap(
    artifact: dict[str, Any],
    *,
    sample_query_lookup: dict[int, str] | None = None,
) -> str:
    """Pretty-print the hard-sample-sorter heatmap + hardness leaderboard."""
    if not artifact or artifact.get("disabled") or not artifact.get("n_observations"):
        return ""

    candidate_order: list[str] = list(artifact.get("candidate_order", []))
    sample_order: list[int] = [int(s) for s in artifact.get("sample_order", [])]
    if not candidate_order or not sample_order:
        return ""

    cells = _cell_index(artifact)
    rasch = artifact.get("rasch") or {}
    theta = rasch.get("theta") or {}
    delta = rasch.get("delta") or {}

    n_cand = artifact.get("n_candidates", len(candidate_order))
    n_samp = artifact.get("n_samples", len(sample_order))
    total_cand = artifact.get("total_candidates", n_cand)
    total_samp = artifact.get("total_samples", n_samp)
    truncated = bool(artifact.get("truncated"))

    label_w = max(_HEATMAP_LABEL_W, min(24, max(len(c) for c in candidate_order)))
    cell_w = _HEATMAP_CELL_W

    cand_str = f"{n_cand} of {total_cand}" + (
        " (truncated)" if truncated and n_cand < total_cand else ""
    )
    samp_str = f"{n_samp} of {total_samp}" + (
        " (truncated)" if truncated and n_samp < total_samp else ""
    )
    lines = [
        f"  individuals : {cand_str}",
        f"  samples     : {samp_str}",
        f"  observed cells : {artifact['n_observations']}",
        f"  legend : {_HIT} hit   {_MISS} miss   {_UNMEASURED} not measured",
    ]

    header_pad = " " * (label_w + 3)
    lines.append("")
    lines.append(header_pad + "hardest ──── sample_id ────→ easiest")
    lines.append(header_pad + "".join(f"{(sid // 10) % 10:>{cell_w}d}" for sid in sample_order))
    lines.append(header_pad + "".join(f"{sid % 10:>{cell_w}d}" for sid in sample_order))

    for cid in candidate_order:
        row_cells = []
        for sid in sample_order:
            hit = cells.get((cid, sid))
            if hit is None:
                row_cells.append(_UNMEASURED * cell_w)
            elif hit:
                row_cells.append(_HIT * cell_w)
            else:
                row_cells.append(_MISS * cell_w)
        t = theta.get(cid)
        theta_str = f"{t:>+5.2f}" if isinstance(t, (int, float)) else "  ?  "
        label = cid[: label_w - 1].ljust(label_w)
        lines.append(f"  {label} {theta_str}  {''.join(row_cells)}")

    top_samples = sample_order[:10]
    if top_samples:
        lookup = sample_query_lookup or {}
        lines.append("")
        lines.append(f"  Hardness leaderboard (top {len(top_samples)})")
        lines.append(f"    {'sample_id':>10s} {'delta':>8s} {'delta_se':>10s}  query")
        lines.append(f"    {'-' * 10} {'-' * 8} {'-' * 10}  {'-' * 40}")
        delta_se = rasch.get("delta_se") or {}
        for sid in top_samples:
            d = float(delta.get(str(sid), 0.0))
            se = float(delta_se.get(str(sid), 0.0))
            query = (lookup.get(sid) or "")[:40]
            lines.append(f"    {sid:>10d} {d:>+8.3f} {se:>10.3f}  {query}")

    return "\n".join(lines)


# --- log.md digest -------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x:.1%}"


def _json_block(label: str, value: Any) -> list[str]:
    if not value:
        return []
    return [
        f"**{label}:**",
        "",
        "```json",
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
        "```",
        "",
    ]


_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def _spark(values: list[float]) -> str:
    """ASCII sparkline for a [0, 1] series using Unicode block elements."""
    if not values:
        return ""
    out: list[str] = []
    for v in values:
        v_clamped = min(1.0, max(0.0, float(v)))
        idx = min(len(_SPARK_BLOCKS) - 1, int(v_clamped * len(_SPARK_BLOCKS)))
        out.append(_SPARK_BLOCKS[idx])
    return "".join(out)


def _render_p_best_trajectory(rd: RoundDigestView) -> list[str]:
    """Render the per-round P(best) trajectory sparkline section.

    Skipped silently when the JSONL stream wasn't available (resumed
    cycles, pre-PoBB rounds).
    """
    if not rd.p_best_trajectory:
        return []
    # Sort by final P(best) desc so the round winner reads first.
    ordered = sorted(
        rd.p_best_trajectory.items(),
        key=lambda kv: -(kv[1][-1] if kv[1] else 0.0),
    )
    lines: list[str] = ["", "P(best) trajectory:", "```"]
    for cid, traj in ordered[:8]:
        if not traj:
            continue
        spark = _spark(traj)
        final = traj[-1] * 100
        suffix = ""
        if final >= 50.0:
            suffix = " [winner]"
        elif final < 5.0:
            suffix = " [stopped]"
        lines.append(f"  {cid[:10]:<10} {spark}  {final:5.1f}%{suffix}")
    lines.append("```")
    return lines


def _render_round(
    rd: RoundDigestView,
    *,
    formula: str | None,
    baseline_composite_fitness: float | None,
) -> list[str]:
    parts: list[str] = [
        f"### Round {rd.round} — {rd.label} ({_fmt_pct(rd.accuracy)})",
        "",
        f"- improved: **{'yes' if rd.improved else 'no'}**",
        f"- hits: {rd.hits}/{rd.total}",
        f"- composite_fitness: `{rd.composite_fitness:.4f}`",
    ]
    if rd.changes_description:
        parts.append(f"- changes: {rd.changes_description}")
    if rd.l1_yield < 1.0:
        n_total = rd.candidates_scored
        n_valid = max(0, n_total - rd.l1_n_no_op - rd.l1_n_duplicate)
        bits: list[str] = []
        if rd.l1_n_no_op:
            bits.append(f"{rd.l1_n_no_op} no-op")
        if rd.l1_n_duplicate:
            bits.append(f"{rd.l1_n_duplicate} dup")
        parts.append(f"- L1 yield: {n_valid}/{n_total} ({', '.join(bits)})")
    composite_fitness_block = render_composite_fitness_block(
        rd.composite_fitness,
        rd.evaluators,
        formula,
        baseline=baseline_composite_fitness,
        use_short_names=False,
    )
    if composite_fitness_block:
        parts += ["", "```", *composite_fitness_block, "```"]
    if rd.l1_critique_text:
        parts += ["", "> " + rd.l1_critique_text.replace("\n", "\n> ")]
    parts += _render_p_best_trajectory(rd)
    parts.append("")
    return parts


def _render_hard_samples(view: HardSamplesView | None) -> list[str]:
    if view is None:
        return []
    heatmap = render_hard_sample_heatmap(
        view.artifact,
        sample_query_lookup=view.sample_query_lookup,
    ).strip()
    if not heatmap:
        return []
    return ["## Hard Samples", "", "```", heatmap, "```", ""]


def _render_forks(forks: tuple[ForkSummaryView, ...]) -> list[str]:
    if not forks:
        return []
    parts = ["## Forks", ""]
    for f in forks:
        short = f.cycle_id.split("_", 1)[-1] if "_" in f.cycle_id else f.cycle_id
        rounds_word = "round" if f.n_rounds == 1 else "rounds"
        line = (
            f"- `{short}` — {f.mode or '(unknown)'} · "
            f"best {_fmt_pct(f.best_accuracy)} "
            f"(baseline {_fmt_pct(f.baseline_accuracy)}, {f.n_rounds} {rounds_word})"
        )
        if f.stop_reason:
            line += f" · {f.stop_reason}"
        parts.append(line)
    parts.append("")
    return parts


def to_markdown(view: LogMdView) -> str:
    """Render a ``LogMdView`` into the full ``log.md`` document."""
    status = view.status
    parts: list[str] = [
        f"# Campaign {status.campaign_id or '(unknown cycle)'}",
        "",
    ]
    if status.parent_session_id:
        parts += [f"_session: `{status.parent_session_id}`_", ""]

    parts += [
        "## Status",
        "",
        f"- status: **{status.status}**",
        f"- stop reason: `{status.stop_reason}`",
        f"- baseline: {_fmt_pct(status.baseline_accuracy)}",
        (
            f"- best: {_fmt_pct(status.best_accuracy)}"
            + (f" (round {status.best_round})" if status.best_round is not None else "")
        ),
    ]
    if view.family_best is not None:
        fb_acc, fb_holder = view.family_best
        if fb_acc > status.best_accuracy and fb_holder != status.campaign_id:
            short = fb_holder.split("_", 1)[-1] if "_" in fb_holder else fb_holder
            parts.append(f"- family best: {_fmt_pct(fb_acc)} (in fork `{short}`)")
    scored_rounds = status.rounds_completed - status.gen_only_rounds
    if status.gen_only_rounds:
        parts.append(
            f"- rounds completed: {scored_rounds} scored (+ {status.gen_only_rounds} gen-only)"
        )
    else:
        parts.append(f"- rounds completed: {status.rounds_completed}")
    if status.started_at:
        parts.append(f"- started: {status.started_at}")
    if status.finished_at:
        parts.append(f"- finished: {status.finished_at}")
    parts += ["", *_render_forks(view.forks), "## Rounds", ""]

    if not view.rounds:
        parts += ["_No rounds yet._", ""]
    for rd in view.rounds:
        parts += _render_round(
            rd,
            formula=view.formula,
            baseline_composite_fitness=view.baseline_composite_fitness,
        )

    parts += _render_hard_samples(view.hard_samples)

    if view.final is not None:
        parts.append("## Final Winner")
        parts.append("")
        parts += _json_block("Prompt fields", view.final.winner_prompt_fields)
        parts += _json_block("Pipeline params", view.final.winner_pipeline_params)

    return "\n".join(parts).rstrip() + "\n"
