"""Phase-handler dispatch: route (phase, event) pairs to per-phase formatters."""

from __future__ import annotations

from collections.abc import Callable

from promptpotter.application.optimization.phases import PhaseEvent
from promptpotter.shared.statistics import min_detectable_effect

from .notebook_primitives import (
    BOLD,
    CYAN,
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
    fmt_pvalue,
)
from .notebook_sp_diff import (
    _build_candidate_flat,
    _CycleDisplayState,
    _flatten_sp_summary,
    _print_sp_diff,
)

__all__ = ["_CycleDisplayState", "_dispatch_phase"]


def _print_init_enter(d: dict, state: _CycleDisplayState) -> None:
    config = d["config"]
    dataset = d["dataset"]
    session = d.get("env")
    schema = session.pipeline_schema if session is not None else None

    warnings = d.get("warnings") or []
    if warnings:
        print()
        for w in warnings:
            print(f"{YELLOW}\u26a0 {BOLD}{w.title}{RESET}")
            print(f"    {YELLOW}{w.detail}{RESET}")

    opt = config.optimization
    state.max_rounds = opt.max_rounds or 0
    state.patience = opt.l1_patience
    state.original_sp_flat = _flatten_sp_summary(
        schema.to_pipeline_params() if schema else None,
    )
    state.node_param_keys = (
        {s: sorted(k) for s, k in schema.node_param_keys().items()} if schema else None
    )

    model = config.optimizer_llm.model or "(default)"
    l2 = "enabled" if opt.enable_l2 else "disabled"
    l3 = "enabled" if opt.enable_l3 else "disabled"
    sample = config.sp_budget_ttest
    state.baseline_total = sample

    print()
    print(_dbox_top())
    print(_dbox_line(f"{BOLD}FEEDBACK CYCLE STARTING{RESET}"))
    print(_dbox_sep())
    print(
        _dbox_line(f"Max rounds     {state.max_rounds or 999!s:<15s}Patience    {state.patience}")
    )
    print(_dbox_line(f"Candidates     {opt.n_variants}"))
    sample_label = f"{sample} of {len(dataset)}"
    mde = min_detectable_effect(sample)
    print(_dbox_line(f"Sample size    {sample_label}"))
    print(_dbox_line(f"Min detectable {YELLOW}\u00b1{mde:.1%}{RESET} (\u03b1=0.05, 80% power)"))
    print(_dbox_line(f"Model          {model}"))
    print(_dbox_line(f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}"))
    critique = "enabled" if opt.enable_critique else "disabled"
    print(_dbox_line(f"Critique       {critique}"))
    print(_dbox_bottom())


def _print_init_exit(d: dict, state: _CycleDisplayState) -> None:
    cycle = d["state"]
    session = d["env"]
    state.baseline_accuracy = cycle.current_accuracy

    # Mix prompt fields from the loaded baseline into the Start column so
    # the SP diff shows what the loop is actually starting from (not just
    # schema pipeline params). Otherwise any candidate that overrides a
    # prompt field would render as a diff against "-".
    prompt_fields = cycle.opt_sp.prompt_field_dict()
    for field_name, value in prompt_fields.items():
        if value:
            state.original_sp_flat[field_name] = str(value)

    cycle_id = (session.cycle_id or "?")[:12]
    samples = len(session.scoring_dataset)
    obs = "ON" if session.obs else "OFF"
    print(
        f"  {GREEN}✓{RESET} Initialized  baseline={cycle.current_accuracy:.1%}  "
        f"cycle={cycle_id}  samples={samples}  obs={obs}"
    )
    crit = cycle.opt_sp.memory.critique_text
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"    {CYAN}Bootstrap critique:{RESET} {preview}")
    resumed = session.resumed_from_round
    if resumed > 0:
        state_parts = []
        critique_chars = len(cycle.opt_sp.memory.critique_text)
        task_context_keys = len(cycle.opt_sp.task_context)
        l2_round = cycle.escalation.l2.round
        if critique_chars:
            state_parts.append(f"critique={critique_chars} chars")
        if task_context_keys:
            state_parts.append(f"task_context={task_context_keys} keys")
        if l2_round:
            state_parts.append(f"l2_round={l2_round}")
        state_suffix = f"  ({', '.join(state_parts)})" if state_parts else ""
        print(f"    Resumed from round {resumed} ({resumed} rounds cached){state_suffix}")
    else:
        print("    Starting fresh (no prior rounds for this cycle)")


def _print_l1_generate_enter(d: dict, state: _CycleDisplayState) -> None:
    acc = d.get("current_accuracy", 0.0)
    preview = d.get("prompt_preview", "").replace("\n", " ").strip()
    if len(preview) > 50:
        preview = preview[:47] + "..."
    if not preview:
        preview = "(empty starting prompt — param-only optimization)"
    n = d.get("n_variants", 0)
    model = d.get("model") or "(default)"
    creativity = d.get("creativity", 0.7)
    crit = "YES" if d.get("has_critique") else "NO"

    new_flat = _flatten_sp_summary(
        d.get("pipeline_params"),
    )
    parent_prompt_fields = d.get("parent_prompt_fields") or {}
    for field_name, value in parent_prompt_fields.items():
        if value:
            new_flat[field_name] = str(value)
    if state.round_num == 0:
        state.previous_sp_flat = state.original_sp_flat.copy()
    else:
        state.previous_sp_flat = state.current_sp_flat.copy()
    state.current_sp_flat = new_flat

    r_label = f"ROUND {state.round_num + 1}/{state.max_rounds or 999}"
    p_label = f"patience {state.l1_stall_count}/{state.patience}"

    print()
    print(_round_rule(r_label, p_label))

    print()
    print(_node_top("GENERATE"))
    print(_node_line(f"Current best    {acc:.1%}"))
    print(_node_line(f"Prompt          {preview}"))
    print(_node_line(f"Candidates      {n}   Creativity: {creativity}   Critique: {crit}"))
    print(_node_line(f"Model           {model}"))

    print(_node_bottom())


def _print_l1_generate_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_candidates", 0)
    source = "loaded from disk" if d.get("loaded_from_disk") else "from LLM"
    state.n_scoring_queries = d.get("n_scoring_queries", 0)
    state.candidates_meta = d.get("candidates", [])

    print(f"  {GREEN}✓{RESET} {n} candidates generated ({source})")

    columns: list[tuple[str, dict[str, str]]] = [
        ("Start", state.original_sp_flat),
        ("Parent", state.current_sp_flat),
    ]
    for c in state.candidates_meta:
        c_flat = _build_candidate_flat(state.current_sp_flat, c)
        columns.append((f"C{c['idx'] + 1}", c_flat))
    print()
    _print_sp_diff(
        columns,
        node_param_keys=state.node_param_keys,
        round_num=state.round_num + 1,
    )


def _print_l1_score_enter(d: dict, state: _CycleDisplayState) -> None:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    n_calls = n_cand * n_q
    calls_label = f"{n_cand} \u00d7 {n_q} = {n_calls} calls"

    scoring_pp = d.get("current_pipeline_params")
    if scoring_pp is not None:
        state.current_pipeline_params = scoring_pp

    print()
    print(_node_top("EVALUATE", calls_label))


def _print_l1_score_exit(d: dict, state: _CycleDisplayState) -> None:
    from promptpotter.shared.statistics import proportion_test

    w_acc = d.get("winner_accuracy", 0.0)
    w_comp = d.get("winner_composite")
    improved = d.get("improved", False)
    action = d.get("next_action", "?")
    scores = d.get("candidate_scores", [])

    for i, s in enumerate(scores):
        s["label"] = f"C{i + 1}"
    non_aborted = [s for s in scores if not s.get("escalation_aborted")]
    best_s = (
        max(non_aborted, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]))
        if non_aborted
        else {}
    )
    winner = best_s.get("label", "?")

    if len(scores) > 3:
        board = _scoreboard(scores, winner, state.baseline_accuracy)
        if board:
            print(board)
    elif scores:
        _ranked = sorted(
            scores, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]), reverse=True
        )
        parts = []
        for s in _ranked:
            lbl = s.get("label", "?")
            acc = s["accuracy"]
            ab = " (aborted)" if s.get("escalation_aborted") else ""
            parts.append(f"{lbl}={acc:.1%}{ab}")
        print(f"  Scoreboard: {' | '.join(parts)}")

    comp_tag = ""
    if w_comp is not None and w_comp != w_acc:
        comp_tag = f"  composite={w_comp:.4f}"

    winner_hits = best_s.get("hits", 0)
    winner_total = best_s.get("total", 0)

    if improved:
        delta = w_acc - state.baseline_accuracy
        bl_hits = round(state.baseline_accuracy * winner_total)
        p = proportion_test(winner_hits, winner_total, bl_hits, winner_total)
        sig_tag = f"  {fmt_pvalue(p)}"
        print(
            f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
            f" (was {state.baseline_accuracy:.1%},"
            f" {_fmt_delta(delta)}){comp_tag}{sig_tag}"
            f"  ->  next: {action}"
        )
        state.baseline_accuracy = w_acc
    else:
        print(f"  {YELLOW}{BOLD}\u26a0 NO IMPROVEMENT{RESET}  best candidate {w_acc:.1%}{comp_tag}")

    crit = d.get("critique_text", "")
    if crit:
        crit_text = crit.replace("\n", " ").strip()
        print(f"  {CYAN}Critique:{RESET} {crit_text}")


def _print_escalation_enter(d: dict, state: _CycleDisplayState) -> None:
    check = d.get("check_name", "?")
    target = d.get("target", "?")
    rate = d.get("degraded_rate", 0)
    wtypes = d.get("warning_types", {})

    print()
    print(_node_top("ESCALATION", f"{check} \u2192 {target}"))
    print(_node_line(f"{YELLOW}Degraded: {rate:.0%} of queries{RESET}"))
    for wt, count in wtypes.items():
        print(_node_line(f"{wt}: {count} occurrences"))
    print(_node_bottom())


def _print_escalation_exit(d: dict, state: _CycleDisplayState) -> None:
    classifications = d.get("classifications", [])
    if classifications:
        print(f"  {CYAN}Warning classifications:{RESET}")
        for c in classifications:
            print(f"    {c['warning_type']}: {c['status']}")


def _print_refine_enter(d: dict, state: _CycleDisplayState) -> None:
    l2r = d.get("l2_round", "?")
    stalls = d.get("l1_stall_count", "?")
    acc = d.get("current_accuracy", 0.0)
    best = d.get("best_accuracy", 0.0)
    params = d.get("current_params", {})

    print()
    print(_node_top("L2 REFINE CONTEXT", f"L2 round {l2r}"))
    print(_node_line(f"L1 stalled {stalls} rounds  |  acc={acc:.1%}  best={best:.1%}"))
    if params:
        parts = []
        for k, v in list(params.items())[:5]:
            vs = str(v)
            if len(vs) > 30:
                vs = vs[:27] + "..."
            parts.append(f"{k}={vs}")
        extra = len(params) - 5
        params_str = ", ".join(parts)
        if extra > 0:
            params_str += f", +{extra} more"
        print(_node_line(f"Current params: {params_str}"))
    else:
        print(_node_line("Current params: (none)"))
    print(_node_line("LLM analyzing failure patterns..."))
    print(_node_bottom())


def _print_refine_exit(d: dict, state: _CycleDisplayState) -> None:
    import json

    n_changes = d.get("param_changes_count", 0)
    tc = f", {GREEN}task_context updated{RESET}" if d.get("task_context_changed") else ""
    _action = d.get("action", "continue")
    probe = f", {CYAN}action={_action}{RESET}" if _action != "continue" else ""
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} L2 decision: {n_changes} param changes{tc}{probe}")
    if desc:
        print(f"    {desc}")

    warned = d.get("warned_queries", 0)
    top_w = d.get("top_warning", "")
    if warned:
        print(
            f"    {YELLOW}\u26a0 {warned} queries with recurring pipeline warnings ({top_w}){RESET}"
        )

    l2_prompt = d.get("l2_prompt", "")
    if l2_prompt:
        print(f"\n  {CYAN}--- L2 PROMPT (sent to LLM) ---{RESET}")
        for line in l2_prompt.split("\n"):
            print(f"  {CYAN}│{RESET} {line}")
        print(f"  {CYAN}--- END PROMPT ---{RESET}")

    l2_resp = d.get("l2_response")
    if l2_resp:
        print(f"\n  {CYAN}--- L2 RESPONSE (raw JSON) ---{RESET}")
        formatted = json.dumps(l2_resp, indent=2)
        for line in formatted.split("\n")[:40]:
            print(f"  {CYAN}│{RESET} {line}")
        print(f"  {CYAN}--- END RESPONSE ---{RESET}")


def _print_probe_enter(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_probe_queries", 0)
    queries = d.get("probe_queries", [])
    print()
    print(_node_top("PROBE ROUND", f"{n} queries"))
    print(_node_line("Testing warned queries with new settings..."))
    for q in queries[:5]:
        print(_node_line(f"  {q[:70]}"))
    if len(queries) > 5:
        print(_node_line(f"  ... +{len(queries) - 5} more"))
    print(_node_bottom())


def _print_probe_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_probed", 0)
    hits = d.get("probe_hits", 0)
    if n:
        rate = hits / n
        color = GREEN if rate > 0.5 else YELLOW
        print(f"  {color}⚡ Probe: {hits}/{n} hits ({rate:.0%}){RESET}")
    else:
        print(f"  {YELLOW}⚡ Probe: no matching queries found{RESET}")


def _print_plan_enter(d: dict, state: _CycleDisplayState) -> None:
    l3r = d.get("l3_round", "?")
    l2_stalls = d.get("l2_stall_count", "?")
    plan = d.get("current_plan_preview", "")
    if len(plan) > 55:
        plan = plan[:52] + "..."

    print()
    print(_node_top("L3 MODIFY PLAN", f"L3 round {l3r}"))
    print(_node_line(f"L2 stalled {l2_stalls} rounds"))
    print(_node_line(f"Current plan: {plan}"))
    print(_node_line("LLM designing new strategy..."))
    print(_node_bottom())


def _print_plan_exit(d: dict, state: _CycleDisplayState) -> None:
    new_plan = d.get("new_plan_preview", "")
    if len(new_plan) > 55:
        new_plan = new_plan[:52] + "..."
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} New plan: {new_plan}")
    if desc:
        print(f"    {desc}")


def _print_backend_warning(d: dict, state: _CycleDisplayState) -> None:
    msg = d.get("message", "")
    advice = d.get("advice", "")
    resets = d.get("degradation_reset_count", 0)
    steps = d.get("problem_steps", [])
    wtypes = d.get("persistent_warning_types", {})

    print()
    print(_dbox_top())
    print(_dbox_line(f"{RED}{BOLD}BACKEND WARNING{RESET}"))
    print(_dbox_sep())
    print(_dbox_line(msg))
    print(_dbox_line(""))
    print(_dbox_line(advice))
    print(_dbox_sep())
    steps_str = ", ".join(steps) if steps else "unknown"
    print(_dbox_line(f"Resets: {resets}  |  Steps: {steps_str}"))
    for wt, count in wtypes.items():
        print(_dbox_line(f"  {wt}: {count} occurrences"))
    print(_dbox_bottom())


_PHASE_HANDLERS: dict[str, Callable] = {
    "init:enter": _print_init_enter,
    "init:exit": _print_init_exit,
    "l1_generate:enter": _print_l1_generate_enter,
    "l1_generate:exit": _print_l1_generate_exit,
    "l1_score:enter": _print_l1_score_enter,
    "l1_score:exit": _print_l1_score_exit,
    "refine_strategy:enter": _print_refine_enter,
    "refine_strategy:exit": _print_refine_exit,
    "modify_plan:enter": _print_plan_enter,
    "modify_plan:exit": _print_plan_exit,
    "escalation:enter": _print_escalation_enter,
    "escalation:exit": _print_escalation_exit,
    "probe_round:enter": _print_probe_enter,
    "probe_round:exit": _print_probe_exit,
    "backend_warning:notify": _print_backend_warning,
}


def _dispatch_phase(event: PhaseEvent, state: _CycleDisplayState) -> None:
    """Route a PhaseEvent to its phase-specific formatter."""
    if event.round is not None:
        state.round_num = event.round
    key = f"{event.phase}:{event.event}"
    handler = _PHASE_HANDLERS.get(key)
    if handler:
        handler(event.data, state)
    else:
        print(f"  [{event.phase.upper()} {event.event}] {event.data}")
