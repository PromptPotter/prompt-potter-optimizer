"""Phase-event renderers — pure functions over view dicts.

Each function takes the JSON ``view`` produced by
``application.campaign.phase_views`` and returns a formatted string. Zero
domain-model knowledge. The dispatch entry point ``render_phase_event(event_record)``
accepts a ``phase_events.jsonl`` line (``{phase, event, round, view, ...}``) and
returns the rendered string, or ``""`` if no renderer is registered.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from promptpotter.application.optimization.sp_diff_view import group_diff_keys
from promptpotter.presentation.views.display_primitives import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RESET,
    YELLOW,
    _dbox_block,
    _fmt_delta,
    _node_block,
    _node_line,
    _node_top,
    _round_rule,
    _scoreboard,
)
from promptpotter.presentation.views.formatting import fmt_pvalue

__all__ = ["render_phase_event"]


# --- SearchPoint diff renderer (inlined from former sp_diff.py) ------------

_SP_DIFF_ABSENT = "-"
_SP_DIFF_UNCHANGED = "·"  # middle dot
_SP_DIFF_VAL_INLINE_MAX = 12


def render_sp_diff(view: dict) -> str:
    """Render N-column diff table from a ``sp_diff`` view-dict.

    ``view`` shape: ``{"columns": [{"label": str, "flat": dict[str,str]}], ...
    "node_param_keys": dict | None, "round_num": int | None}``
    """
    columns_in = view.get("columns") or []
    if len(columns_in) < 2:
        return ""

    columns: list[tuple[str, dict[str, str]]] = [(c["label"], c["flat"]) for c in columns_in]
    node_param_keys = view.get("node_param_keys")
    round_num = view.get("round_num")

    all_keys = {k for _, d in columns for k in d}
    diff_keys = sorted(k for k in all_keys if len({d.get(k) for _, d in columns}) > 1)
    if not diff_keys:
        return ""

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

    out: list[str] = []
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


def _truncate_kv(items: list[tuple[str, str]], n_max: int = 5, val_max: int = 30) -> str:
    """Render ``[(k, v), ...]`` as ``"k=v, k=v, ..., +N more"`` with value truncation."""
    parts = [
        f"{k}={v if len(v) <= val_max else v[: val_max - 3] + '...'}" for k, v in items[:n_max]
    ]
    extra = len(items) - n_max
    return ", ".join(parts) + (f", +{extra} more" if extra > 0 else "")


def _render_init_enter(view: dict) -> str:
    warnings = view.get("warnings") or []
    out = [""] if warnings else []
    for w in warnings:
        out.append(f"{YELLOW}⚠ {BOLD}{w['title']}{RESET}")
        out.append(f"    {YELLOW}{w['detail']}{RESET}")

    max_rounds = view["max_rounds"] or 999
    l2 = "enabled" if view["l2_enabled"] else "disabled"
    l3 = "enabled" if view["l3_enabled"] else "disabled"
    out.append("")
    out.append(
        _dbox_block(
            f"{BOLD}FEEDBACK CYCLE STARTING{RESET}",
            f"Max rounds     {max_rounds!s:<15s}Patience    {view['patience']}",
            f"Candidates     {view['n_variants']}",
            f"Sample size    {view['sp_budget_ttest']} of {view['dataset_size']}",
            f"Min detectable {YELLOW}±{view['mde']:.1%}{RESET} (α=0.05, 80% power)",  # noqa: RUF001
            f"Model          {view['model']}",
            f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}",
        )
    )
    return "\n".join(out)


def _render_init_exit(view: dict) -> str:
    obs = "ON" if view["obs_on"] else "OFF"
    out = [
        f"  {GREEN}✓{RESET} Initialized  baseline={view['baseline_acc']:.1%}  "
        f"cycle={view['cycle_id_short']}  samples={view['samples']}  obs={obs}"
    ]
    if crit := view.get("bootstrap_critique"):
        out.append(f"    {CYAN}Bootstrap L1 critique:{RESET} {crit}")
    if resumed := view.get("resumed_from_round", 0):
        parts = [
            label.format(v)
            for k, label in (
                ("l1_critique_chars", "l1_critique={} chars"),
                ("task_context_keys", "task_context={} keys"),
                ("l2_round", "l2_round={}"),
            )
            if (v := view.get(k))
        ]
        suffix = f"  ({', '.join(parts)})" if parts else ""
        out.append(f"    Resumed from round {resumed} ({resumed} rounds cached){suffix}")
    else:
        out.append("    Starting fresh (no prior rounds for this cycle)")
    return "\n".join(out)


def _render_l1_generate_enter(view: dict) -> str:
    crit = "YES" if view["has_l1_critique"] else "NO"
    return "\n".join(
        [
            "",
            _round_rule(
                f"ROUND {view['round']}/{view['max_rounds'] or 999}",
                f"patience {view['l1_stall_count']}/{view['patience']}",
            ),
            "",
            _node_block(
                "GENERATE",
                f"Current best    {view['current_acc']:.1%}",
                f"Prompt          {view['prompt_preview']}",
                f"Candidates      {view['n_variants']}   "
                f"Creativity: {view['creativity']}   Critique: {crit}",
                f"Model           {view['model']}",
            ),
        ]
    )


def _render_l1_generate_exit(view: dict) -> str:
    src = "loaded from disk" if view["source"] == "disk" else "from LLM"
    return "\n".join(
        [
            f"  {GREEN}✓{RESET} {view['n_candidates']} candidates generated ({src})",
            "",
            render_sp_diff(view["sp_diff"]),
        ]
    )


def _render_l1_score_enter(view: dict) -> str:
    label = f"{view['n_candidates']} × {view['n_queries']} = {view['n_calls']} calls"  # noqa: RUF001
    return "\n" + _node_top("EVALUATE", label)


def _render_l1_score_exit(view: dict) -> str:
    scores = view["scores"]
    winner_label = view["winner_label"]
    baseline_acc = view["baseline_acc"]

    out: list[str] = []
    if len(scores) > 3:
        if board := _scoreboard(scores, winner_label, baseline_acc):
            out.append(board)
    elif scores:
        parts = [
            f"{s['label']}={s['accuracy']:.1%}{' (aborted)' if s['escalation_aborted'] else ''}"
            for s in sorted(
                scores,
                key=lambda s: (s.get("composite") or s["accuracy"], s["accuracy"]),
                reverse=True,
            )
        ]
        out.append(f"  Scoreboard: {' | '.join(parts)}")

    w_acc = view["winner_accuracy"]
    w_comp = view.get("winner_composite")
    comp_tag = f"  composite={w_comp:.4f}" if w_comp is not None and w_comp != w_acc else ""

    if view["improved"]:
        p = view.get("p_value")
        sig_tag = f"  {fmt_pvalue(p)}" if p is not None else ""
        out.append(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
            f" (was {baseline_acc:.1%}, {_fmt_delta(view['delta'])}){comp_tag}{sig_tag}"
            f"  ->  next: {view['next_action']}"
        )
    else:
        out.append(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate {w_acc:.1%}{comp_tag}")

    if crit := (view.get("l1_critique_text") or "").replace("\n", " ").strip():
        out.append(f"  {CYAN}L1 Critique:{RESET} {crit}")
    return "\n".join(out)


def _render_escalation_enter(view: dict) -> str:
    extras = [
        f"{wt}: {count} occurrences" for wt, count in (view.get("warning_types") or {}).items()
    ]
    return "\n" + _node_block(
        "ESCALATION",
        f"{YELLOW}Degraded: {view['degraded_rate']:.0%} of queries{RESET}",
        *extras,
        label_right=f"{view['check_name']} → {view['target']}",
    )


def _render_escalation_exit(view: dict) -> str:
    classifications = view.get("classifications") or []
    if not classifications:
        return ""
    out = [f"  {CYAN}Warning classifications:{RESET}"]
    out.extend(f"    {c['warning_type']}: {c['status']}" for c in classifications)
    return "\n".join(out)


def _render_refine_enter(view: dict) -> str:
    params = view.get("current_params") or {}
    params_line = (
        f"Current params: {_truncate_kv(list(params.items()))}"
        if params
        else "Current params: (none)"
    )
    return "\n" + _node_block(
        "L2 REFINE CONTEXT",
        f"L1 stalled {view['l1_stall_count']} rounds  |  "
        f"acc={view['current_acc']:.1%}  best={view['best_acc']:.1%}",
        params_line,
        "LLM analyzing failure patterns...",
        label_right=f"L2 round {view['l2_round']}",
    )


def _render_refine_exit(view: dict) -> str:
    tc = f", {GREEN}task_context updated{RESET}" if view["task_context_changed"] else ""
    action = view["action"]
    probe = f", {CYAN}action={action}{RESET}" if action != "continue" else ""
    out = [f"  {GREEN}✓{RESET} L2 decision: {view['param_changes_count']} param changes{tc}{probe}"]
    if desc := (view.get("changes_description", "") or ""):
        out.append(f"    {desc}")
    if warned := view.get("warned_queries", 0):
        top_w = view.get("top_warning", "")
        out.append(
            f"    {YELLOW}⚠ {warned} queries with recurring pipeline warnings ({top_w}){RESET}"
        )

    if l2_prompt := (view.get("l2_prompt", "") or ""):
        out.append(f"\n  {CYAN}--- L2 PROMPT (sent to LLM) ---{RESET}")
        out.extend(f"  {CYAN}│{RESET} {line}" for line in l2_prompt.split("\n"))
        out.append(f"  {CYAN}--- END PROMPT ---{RESET}")

    if (l2_resp := view.get("l2_response_json")) is not None:
        out.append(f"\n  {CYAN}--- L2 RESPONSE (raw JSON) ---{RESET}")
        out.extend(
            f"  {CYAN}│{RESET} {line}" for line in json.dumps(l2_resp, indent=2).split("\n")[:40]
        )
        out.append(f"  {CYAN}--- END RESPONSE ---{RESET}")
    return "\n".join(out)


def _render_probe_enter(view: dict) -> str:
    queries = view.get("probe_queries") or []
    extras = [f"  {q[:70]}" for q in queries[:5]]
    if len(queries) > 5:
        extras.append(f"  ... +{len(queries) - 5} more")
    return "\n" + _node_block(
        "PROBE ROUND",
        "Testing warned queries with new settings...",
        *extras,
        label_right=f"{view['n_probe_queries']} queries",
    )


def _render_probe_exit(view: dict) -> str:
    n = view["n_probed"]
    if not n:
        return f"  {YELLOW}⚡ Probe: no matching queries found{RESET}"
    hits = view["probe_hits"]
    rate = hits / n
    color = GREEN if rate > 0.5 else YELLOW
    return f"  {color}⚡ Probe: {hits}/{n} hits ({rate:.0%}){RESET}"


def _render_plan_enter(view: dict) -> str:
    return "\n" + _node_block(
        "L3 MODIFY PLAN",
        f"L2 stalled {view['l2_stall_count']} rounds",
        f"Current plan: {view['current_plan_preview']}",
        "LLM designing new strategy...",
        label_right=f"L3 round {view['l3_round']}",
    )


def _render_plan_exit(view: dict) -> str:
    out = [f"  {GREEN}✓{RESET} New plan: {view['new_plan_preview']}"]
    if desc := (view.get("changes_description", "") or ""):
        out.append(f"    {desc}")
    return "\n".join(out)


_RENDERERS: dict[str, Callable[[dict], str]] = {
    "init:enter": _render_init_enter,
    "init:exit": _render_init_exit,
    "l1_generate:enter": _render_l1_generate_enter,
    "l1_generate:exit": _render_l1_generate_exit,
    "l1_score:enter": _render_l1_score_enter,
    "l1_score:exit": _render_l1_score_exit,
    "escalation:enter": _render_escalation_enter,
    "escalation:exit": _render_escalation_exit,
    "refine_strategy:enter": _render_refine_enter,
    "refine_strategy:exit": _render_refine_exit,
    "probe_round:enter": _render_probe_enter,
    "probe_round:exit": _render_probe_exit,
    "modify_plan:enter": _render_plan_enter,
    "modify_plan:exit": _render_plan_exit,
}


def render_phase_event(event_record: dict) -> str:
    """Render a phase_events.jsonl record. Returns ``""`` if no renderer."""
    phase = event_record.get("phase", "")
    event = event_record.get("event", "")
    view = event_record.get("view") or {}
    if renderer := _RENDERERS.get(f"{phase}:{event}"):
        return renderer(view) or ""
    # Unknown phase — still emit a one-liner so debugging is possible.
    return f"  [{phase.upper()} {event}] {view}"
