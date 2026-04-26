"""Phase-event renderers — pure functions over view dicts.

Mirrors the ``dashboard.py`` pattern: each function takes the JSON
``view`` produced by ``application.campaign.phase_views`` and returns a
formatted string. Zero domain-model knowledge. The dispatch entry point
``render_phase_event(event_record)`` accepts a ``phase_events.jsonl`` line
(``{phase, event, round, view, ...}``) and returns the rendered string,
or ``""`` if no renderer is registered.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from promptpotter.presentation.views.display_primitives import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _dbox_bottom,
    _dbox_line,
    _dbox_sep,
    _dbox_top,
    _fmt_delta,
    _node_bottom,
    _node_line,
    _node_top,
    _round_rule,
    _scoreboard,
)
from promptpotter.presentation.views.formatting import fmt_pvalue
from promptpotter.presentation.views.sp_diff import render_sp_diff

__all__ = ["render_phase_event"]


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _render_init_enter(view: dict) -> str:
    lines: list[str] = []
    warnings = view.get("warnings") or []
    if warnings:
        lines.append("")
        for w in warnings:
            lines.append(f"{YELLOW}⚠ {BOLD}{w['title']}{RESET}")
            lines.append(f"    {YELLOW}{w['detail']}{RESET}")

    lines.append("")
    lines.append(_dbox_top())
    lines.append(_dbox_line(f"{BOLD}FEEDBACK CYCLE STARTING{RESET}"))
    lines.append(_dbox_sep())
    max_rounds = view["max_rounds"] or 999
    lines.append(_dbox_line(f"Max rounds     {max_rounds!s:<15s}Patience    {view['patience']}"))
    lines.append(_dbox_line(f"Candidates     {view['n_variants']}"))
    sample_label = f"{view['sp_budget_ttest']} of {view['dataset_size']}"
    lines.append(_dbox_line(f"Sample size    {sample_label}"))
    mde = view["mde"]
    mde_line = f"Min detectable {YELLOW}±{mde:.1%}{RESET} (α=0.05, 80% power)"  # noqa: RUF001
    lines.append(_dbox_line(mde_line))
    lines.append(_dbox_line(f"Model          {view['model']}"))
    l2 = "enabled" if view["l2_enabled"] else "disabled"
    l3 = "enabled" if view["l3_enabled"] else "disabled"
    lines.append(_dbox_line(f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}"))
    crit = "enabled" if view["critique_enabled"] else "disabled"
    lines.append(_dbox_line(f"L1 Critique    {crit}"))
    lines.append(_dbox_bottom())
    return "\n".join(lines)


def _render_init_exit(view: dict) -> str:
    cycle_id = view["cycle_id_short"]
    samples = view["samples"]
    obs = "ON" if view["obs_on"] else "OFF"
    out: list[str] = [
        f"  {GREEN}✓{RESET} Initialized  baseline={view['baseline_acc']:.1%}  "
        f"cycle={cycle_id}  samples={samples}  obs={obs}"
    ]
    crit = view.get("bootstrap_critique") or ""
    if crit:
        out.append(f"    {CYAN}Bootstrap L1 critique:{RESET} {crit}")
    resumed = view.get("resumed_from_round", 0)
    if resumed > 0:
        parts = []
        if view.get("l1_critique_chars"):
            parts.append(f"l1_critique={view['l1_critique_chars']} chars")
        if view.get("task_context_keys"):
            parts.append(f"task_context={view['task_context_keys']} keys")
        if view.get("l2_round"):
            parts.append(f"l2_round={view['l2_round']}")
        suffix = f"  ({', '.join(parts)})" if parts else ""
        out.append(f"    Resumed from round {resumed} ({resumed} rounds cached){suffix}")
    else:
        out.append("    Starting fresh (no prior rounds for this cycle)")
    return "\n".join(out)


def _render_l1_generate_enter(view: dict) -> str:
    r = view["round"]
    max_rounds = view["max_rounds"] or 999
    crit = "YES" if view["has_l1_critique"] else "NO"

    out: list[str] = [
        "",
        _round_rule(
            f"ROUND {r}/{max_rounds}",
            f"patience {view['l1_stall_count']}/{view['patience']}",
        ),
        "",
        _node_top("GENERATE"),
        _node_line(f"Current best    {view['current_acc']:.1%}"),
        _node_line(f"Prompt          {view['prompt_preview']}"),
        _node_line(
            f"Candidates      {view['n_variants']}   "
            f"Creativity: {view['creativity']}   Critique: {crit}"
        ),
        _node_line(f"Model           {view['model']}"),
        _node_bottom(),
    ]
    return "\n".join(out)


def _render_l1_generate_exit(view: dict) -> str:
    n = view["n_candidates"]
    src = "loaded from disk" if view["source"] == "disk" else "from LLM"
    out = [f"  {GREEN}✓{RESET} {n} candidates generated ({src})", ""]
    out.append(render_sp_diff(view["sp_diff"]))
    return "\n".join(line for line in out if line is not None)


def _render_l1_score_enter(view: dict) -> str:
    n_cand = view["n_candidates"]
    n_q = view["n_queries"]
    n_calls = view["n_calls"]
    label = f"{n_cand} × {n_q} = {n_calls} calls"  # noqa: RUF001
    return "\n" + _node_top("EVALUATE", label)


def _render_l1_score_exit(view: dict) -> str:
    scores = view["scores"]
    winner_label = view["winner_label"]
    baseline_acc = view["baseline_acc"]

    out: list[str] = []
    if len(scores) > 3:
        board = _scoreboard(scores, winner_label, baseline_acc)
        if board:
            out.append(board)
    elif scores:
        ranked = sorted(
            scores,
            key=lambda s: (s.get("composite") or s["accuracy"], s["accuracy"]),
            reverse=True,
        )
        parts: list[str] = []
        for s in ranked:
            ab = " (aborted)" if s["escalation_aborted"] else ""
            parts.append(f"{s['label']}={s['accuracy']:.1%}{ab}")
        out.append(f"  Scoreboard: {' | '.join(parts)}")

    w_acc = view["winner_accuracy"]
    w_comp = view.get("winner_composite")
    comp_tag = f"  composite={w_comp:.4f}" if w_comp is not None and w_comp != w_acc else ""

    if view["improved"]:
        delta = view["delta"]
        p = view.get("p_value")
        sig_tag = f"  {fmt_pvalue(p)}" if p is not None else ""
        out.append(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
            f" (was {baseline_acc:.1%}, {_fmt_delta(delta)}){comp_tag}{sig_tag}"
            f"  ->  next: {view['next_action']}"
        )
    else:
        out.append(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate {w_acc:.1%}{comp_tag}")

    crit = (view.get("l1_critique_text") or "").replace("\n", " ").strip()
    if crit:
        out.append(f"  {CYAN}L1 Critique:{RESET} {crit}")
    return "\n".join(out)


def _render_escalation_enter(view: dict) -> str:
    out = [
        "",
        _node_top("ESCALATION", f"{view['check_name']} → {view['target']}"),
        _node_line(f"{YELLOW}Degraded: {view['degraded_rate']:.0%} of queries{RESET}"),
    ]
    for wt, count in (view.get("warning_types") or {}).items():
        out.append(_node_line(f"{wt}: {count} occurrences"))
    out.append(_node_bottom())
    return "\n".join(out)


def _render_escalation_exit(view: dict) -> str:
    classifications = view.get("classifications") or []
    if not classifications:
        return ""
    out = [f"  {CYAN}Warning classifications:{RESET}"]
    for c in classifications:
        out.append(f"    {c['warning_type']}: {c['status']}")
    return "\n".join(out)


def _render_refine_enter(view: dict) -> str:
    out = [
        "",
        _node_top("L2 REFINE CONTEXT", f"L2 round {view['l2_round']}"),
        _node_line(
            f"L1 stalled {view['l1_stall_count']} rounds  |  "
            f"acc={view['current_acc']:.1%}  best={view['best_acc']:.1%}"
        ),
    ]
    params = view.get("current_params") or {}
    if params:
        items = list(params.items())
        parts: list[str] = []
        for k, v in items[:5]:
            vs = v if len(v) <= 30 else v[:27] + "..."
            parts.append(f"{k}={vs}")
        extra = len(items) - 5
        params_str = ", ".join(parts)
        if extra > 0:
            params_str += f", +{extra} more"
        out.append(_node_line(f"Current params: {params_str}"))
    else:
        out.append(_node_line("Current params: (none)"))
    out.append(_node_line("LLM analyzing failure patterns..."))
    out.append(_node_bottom())
    return "\n".join(out)


def _render_refine_exit(view: dict) -> str:
    n_changes = view["param_changes_count"]
    tc = f", {GREEN}task_context updated{RESET}" if view["task_context_changed"] else ""
    action = view["action"]
    probe = f", {CYAN}action={action}{RESET}" if action != "continue" else ""
    desc = view.get("changes_description", "") or ""
    out: list[str] = [f"  {GREEN}✓{RESET} L2 decision: {n_changes} param changes{tc}{probe}"]
    if desc:
        out.append(f"    {desc}")
    warned = view.get("warned_queries", 0)
    top_w = view.get("top_warning", "")
    if warned:
        out.append(
            f"    {YELLOW}⚠ {warned} queries with recurring pipeline warnings ({top_w}){RESET}"
        )

    l2_prompt = view.get("l2_prompt", "") or ""
    if l2_prompt:
        out.append(f"\n  {CYAN}--- L2 PROMPT (sent to LLM) ---{RESET}")
        for line in l2_prompt.split("\n"):
            out.append(f"  {CYAN}│{RESET} {line}")
        out.append(f"  {CYAN}--- END PROMPT ---{RESET}")

    l2_resp = view.get("l2_response_json")
    if l2_resp is not None:
        out.append(f"\n  {CYAN}--- L2 RESPONSE (raw JSON) ---{RESET}")
        formatted = json.dumps(l2_resp, indent=2)
        for line in formatted.split("\n")[:40]:
            out.append(f"  {CYAN}│{RESET} {line}")
        out.append(f"  {CYAN}--- END RESPONSE ---{RESET}")
    return "\n".join(out)


def _render_probe_enter(view: dict) -> str:
    n = view["n_probe_queries"]
    queries = view.get("probe_queries") or []
    out = [
        "",
        _node_top("PROBE ROUND", f"{n} queries"),
        _node_line("Testing warned queries with new settings..."),
    ]
    for q in queries[:5]:
        out.append(_node_line(f"  {q[:70]}"))
    if len(queries) > 5:
        out.append(_node_line(f"  ... +{len(queries) - 5} more"))
    out.append(_node_bottom())
    return "\n".join(out)


def _render_probe_exit(view: dict) -> str:
    n = view["n_probed"]
    hits = view["probe_hits"]
    if n:
        rate = hits / n
        color = GREEN if rate > 0.5 else YELLOW
        return f"  {color}⚡ Probe: {hits}/{n} hits ({rate:.0%}){RESET}"
    return f"  {YELLOW}⚡ Probe: no matching queries found{RESET}"


def _render_plan_enter(view: dict) -> str:
    out = [
        "",
        _node_top("L3 MODIFY PLAN", f"L3 round {view['l3_round']}"),
        _node_line(f"L2 stalled {view['l2_stall_count']} rounds"),
        _node_line(f"Current plan: {view['current_plan_preview']}"),
        _node_line("LLM designing new strategy..."),
        _node_bottom(),
    ]
    return "\n".join(out)


def _render_plan_exit(view: dict) -> str:
    out = [f"  {GREEN}✓{RESET} New plan: {view['new_plan_preview']}"]
    desc = view.get("changes_description", "") or ""
    if desc:
        out.append(f"    {desc}")
    return "\n".join(out)


def _render_backend_warning(view: dict) -> str:
    msg = view.get("message", "")
    advice = view.get("advice", "")
    resets = view.get("degradation_reset_count", 0)
    steps = view.get("problem_steps") or []
    wtypes = view.get("persistent_warning_types") or {}

    out = [
        "",
        _dbox_top(),
        _dbox_line(f"{RED}{BOLD}BACKEND WARNING{RESET}"),
        _dbox_sep(),
        _dbox_line(msg),
        _dbox_line(""),
        _dbox_line(advice),
        _dbox_sep(),
    ]
    steps_str = ", ".join(steps) if steps else "unknown"
    out.append(_dbox_line(f"Resets: {resets}  |  Steps: {steps_str}"))
    for wt, count in wtypes.items():
        out.append(_dbox_line(f"  {wt}: {count} occurrences"))
    out.append(_dbox_bottom())
    return "\n".join(out)


_RENDERERS: dict[str, Callable[[dict], str]] = {
    "init:enter": _render_init_enter,
    "init:exit": _render_init_exit,
    "l1_generate:enter": _render_l1_generate_enter,
    "l1_generate:exit": _render_l1_generate_exit,
    "l1_score:enter": _render_l1_score_enter,
    "l1_score:exit": _render_l1_score_exit,
    "refine_strategy:enter": _render_refine_enter,
    "refine_strategy:exit": _render_refine_exit,
    "modify_plan:enter": _render_plan_enter,
    "modify_plan:exit": _render_plan_exit,
    "escalation:enter": _render_escalation_enter,
    "escalation:exit": _render_escalation_exit,
    "probe_round:enter": _render_probe_enter,
    "probe_round:exit": _render_probe_exit,
    "backend_warning:notify": _render_backend_warning,
}


def render_phase_event(event_record: dict) -> str:
    """Render a phase_events.jsonl record. Returns ``""`` if no renderer."""
    phase = event_record.get("phase", "")
    event = event_record.get("event", "")
    view = event_record.get("view") or {}
    renderer = _RENDERERS.get(f"{phase}:{event}")
    if renderer is None:
        # Unknown phase — still emit a one-liner so debugging is possible.
        return f"  [{phase.upper()} {event}] {event_record.get('view', {})}"
    rendered = renderer(view)
    return rendered or ""


# Suppress DIM unused-warning while we keep the symbol in scope for possible
# future renderers (group separator in sp_diff for example uses it).
_ = DIM
