"""Notebook phase dispatch + analytics.

Three concerns:
- ``show_*`` analytics functions (progress, leaderboards, axis profiles,
  campaign summary, flip tracking, lineage) — called directly from
  notebook cells and CLI.
- ``_CycleDisplayState`` + SP-diff helpers — mutable state + 3-column
  diff table rendering threaded through phase handlers.
- ``_print_*`` phase handlers + ``_dispatch_phase`` — route PhaseEvent
  objects to per-phase formatters during a feedback cycle.

Imports only from ``notebook_primitives``. The ``NotebookDisplay``
callback class in ``notebook_display`` consumes
``_CycleDisplayState``/``_dispatch_phase``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from promptpotter.application.optimization.phases import PhaseEvent
from promptpotter.shared.statistics import min_detectable_effect

from .notebook_primitives import (
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
    _node_bottom,
    _node_line,
    _node_top,
    _pp_val,
    _round_rule,
    _scoreboard,
    fmt_pvalue,
)
from .notebook_primitives import (
    _fmt_delta as _fmt_delta_primitive,
)

_fmt_delta = _fmt_delta_primitive


# ---------------------------------------------------------------------------
# show_* analytics — called directly from notebook cells and CLI
# ---------------------------------------------------------------------------


def show_progress(campaign_rounds: list, window: int = 8) -> None:
    """Print training-style progress summary after each round.

    Thin wrapper over ``presentation.views.render_progress`` — the same
    renderer the CLI ``show-results`` uses.
    """
    from promptpotter.presentation.views import render_progress

    print(render_progress(campaign_rounds, window=window))


def format_pipeline_overrides(
    pipeline_params: dict | None,
    pipeline_schema=None,
) -> None:
    """Print pipeline_params as a copy-paste ready ``pipeline_overrides`` dict.

    Output uses nested format: ``{"node_name": {"param": value}}``.
    """
    if not pipeline_params:
        return

    node_entries: list[tuple[str, dict]] = []
    for key, val in pipeline_params.items():
        if key == "steps" or not isinstance(val, dict):
            continue
        tunable = {}
        if pipeline_schema:
            node = pipeline_schema.get_node(key)
            if node:
                tunable = {k: v for k, v in val.items() if k in node.param_keys}
        if not tunable:
            tunable = val
        if tunable:
            node_entries.append((key, tunable))

    if not node_entries:
        return

    print(f"\n  {CYAN}Copy-paste pipeline_overrides:{RESET}")
    print(f"  {DIM}{'─' * 60}{RESET}")
    print('  "pipeline_overrides": {')
    for node_name, params in node_entries:
        print(f'      "{node_name}": {{')
        for param, val in params.items():
            print(f'          "{param}": {val!r},')
        print("      },")
    print("  }")
    print(f"  {DIM}{'─' * 60}{RESET}")


def show_campaign_summary(campaign_rounds: list) -> None:
    """Print campaign comparison table.

    Thin wrapper over ``presentation.views.render_campaign_summary``.
    """
    from promptpotter.presentation.views import render_campaign_summary

    print(render_campaign_summary(campaign_rounds))


def show_flip_tracking(campaign_rounds: list) -> None:
    """Compare first vs last round, show per-query flips.

    Thin wrapper over ``presentation.views.render_flip_tracking``.
    """
    from promptpotter.presentation.views import render_flip_tracking

    out = render_flip_tracking(campaign_rounds)
    print(out if out else "Need at least 2 rounds with results for flip tracking.")


def show_lineage_chain(campaign_rounds: list) -> None:
    """Display OptSearchPoint lineage chain across rounds.

    Thin wrapper over ``presentation.views.render_lineage``.
    """
    from promptpotter.presentation.views import render_lineage

    print(render_lineage(campaign_rounds))


# ---------------------------------------------------------------------------
# Display state — tracks cycle metadata across phase callbacks (display-only)
# ---------------------------------------------------------------------------


@dataclass
class _CycleDisplayState:
    """Mutable display state threaded through phase/callback closures.

    Populated exclusively from PhaseEvent data — never touches services.
    """

    max_rounds: int = 0
    patience: int = 0
    l1_stall_count: int = 0
    round_num: int = 0
    baseline_accuracy: float = 0.0
    baseline_total: int = 0  # sample count for significance tests
    candidates_meta: list = field(default_factory=list)  # from l1_generate exit
    n_scoring_queries: int = 0  # from generate:exit, used in evaluate:enter banner
    current_pipeline_params: dict | None = None  # raw pp for candidate eval callback
    # 3-column SP diff tracking (flattened dot-notation dicts)
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    previous_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    node_param_keys: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# SP flatten + diff helpers
# ---------------------------------------------------------------------------


def _flatten_sp_summary(
    pp: dict | None,
) -> dict[str, str]:
    """Flatten SearchPoint dimensions into dot-notation display dict.

    - Scalar pipeline params: ``key`` → formatted value
    - JSON Schema params (type=object with properties): expand to
      ``key.field_name`` → description string
    - Mutation-tuple lists: expand ``['+', name, ...]`` to ``key.name`` → desc
    """
    flat: dict[str, str] = {}

    for k, v in (pp or {}).items():
        if k == "steps":
            continue
        if isinstance(v, dict) and v.get("type") == "object" and "properties" in v:
            for prop_name, prop_def in v["properties"].items():
                desc = prop_def.get("description", prop_def.get("type", "?"))
                flat[f"{k}.{prop_name}"] = desc
        elif isinstance(v, list) and v and isinstance(v[0], list):
            for mutation in v:
                if not mutation:
                    continue
                op = mutation[0]
                if op == "+" and len(mutation) >= 5:
                    flat[f"{k}.{mutation[1]}"] = mutation[4]
                elif op == "~" and len(mutation) >= 6:
                    flat[f"{k}.{mutation[2]}"] = mutation[5]
        elif isinstance(v, dict):
            for sub_k, sub_v in v.items():
                flat[sub_k] = _pp_val(sub_v)
        else:
            flat[k] = _pp_val(v)
    return flat


_ABSENT = "-"
_UNCHANGED = "\u00b7"  # middle dot
_VAL_INLINE_MAX = 12  # values longer than this get a lookup code


def _build_candidate_flat(
    parent: dict[str, str],
    candidate_meta: dict,
) -> dict[str, str]:
    """Merge candidate overrides onto parent flat dict.

    When a candidate overrides a schema key, parent's dot-notation
    children for that key are removed first, then the candidate's
    expanded fields are added. Prompt-field rewrites (persona,
    task_intent, …) ride on ``candidate_meta["prompt_fields"]`` and are
    overlaid as top-level keys.
    """
    flat = parent.copy()
    pp = candidate_meta.get("pipeline_params_override")
    if pp:
        for k in pp:
            prefix = f"{k}."
            to_remove = [pk for pk in flat if pk.startswith(prefix)]
            for pk in to_remove:
                del flat[pk]
        override_flat = _flatten_sp_summary(pp)
        flat.update(override_flat)
    prompt_fields = candidate_meta.get("prompt_fields") or {}
    for field_name, value in prompt_fields.items():
        if value:
            flat[field_name] = str(value)
    return flat


def _group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group diff keys by pipeline node in execution order."""
    if not node_param_keys:
        return [("", diff_keys)]

    key_to_node: dict[str, str] = {}
    for sname, keys in node_param_keys.items():
        for k in keys:
            key_to_node[k] = sname
    for k in diff_keys:
        if k not in key_to_node:
            base = k.split(".")[0]
            if base in key_to_node:
                key_to_node[k] = key_to_node[base]

    groups: dict[str, list[str]] = {sname: [] for sname in node_param_keys}
    groups[""] = []
    for k in diff_keys:
        sname = key_to_node.get(k, "")
        groups.setdefault(sname, []).append(k)

    return [(sname, sorted(keys)) for sname, keys in groups.items() if keys]


def _print_sp_diff(
    columns: list[tuple[str, dict[str, str]]],
    node_param_keys: dict[str, list[str]] | None = None,
    round_num: int | None = None,
) -> None:
    """Print N-column diff table with lookup codes for long values.

    Only rows where at least one column differs are shown. Short values
    (<=12 chars) are shown inline; longer values get a letter code
    ``[a]``..``[z]`` with full text in a legend below the table. When
    ``node_param_keys`` is provided, rows are grouped by pipeline node.
    """
    if len(columns) < 2:
        return

    all_keys: set[str] = set()
    for _, d in columns:
        all_keys.update(d.keys())
    diff_keys = []
    for k in sorted(all_keys):
        vals = [d.get(k) for _, d in columns]
        if len(set(vals)) > 1:
            diff_keys.append(k)
    if not diff_keys:
        return

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
            return _ABSENT
        if val == prior:
            return _UNCHANGED
        if len(val) <= _VAL_INLINE_MAX:
            return val
        return _get_code(val)

    col_w = max(8, max(len(label) for label, _ in columns) + 2)
    # Bump col_w to fit any inline value (<= _VAL_INLINE_MAX) so short-but-
    # wider-than-label values like "**{answer}**" don't overflow into the
    # next cell.
    widest_inline = max(
        (
            len(d[k])
            for k in diff_keys
            for _, d in columns
            if k in d and len(d[k]) <= _VAL_INLINE_MAX
        ),
        default=0,
    )
    col_w = max(col_w, widest_inline + 2)
    max_key = max(len(k) for k in diff_keys)

    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    print(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(f"{label:<{col_w}}" for label, _ in columns)
    print(_node_line(hdr))

    groups = _group_diff_keys(diff_keys, node_param_keys)
    for _gi, (node_name, group_keys) in enumerate(groups):
        if node_name and len(groups) > 1:
            sep = f"{'─── ' + node_name + ' ':─<{max_key + 2}}"
            print(_node_line(f"{DIM}{sep}{RESET}"))
        for k in group_keys:
            cells = []
            prev_val = None
            for _, d in columns:
                v = d.get(k)
                cells.append(_cell(v, prev_val))
                prev_val = v
            row = f"{k:>{max_key}}  " + "".join(f"{c:<{col_w}}" for c in cells)
            print(_node_line(row))

    if legend:
        print(_node_line(""))
        print(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            print(_node_line(f"  {code} {full}"))


# ---------------------------------------------------------------------------
# Phase handlers — one per (phase, event) pair
# ---------------------------------------------------------------------------


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
    loop_state = d["state"]
    loop_env = d["env"]
    state.baseline_accuracy = loop_state.current_accuracy

    # Mix prompt fields from the loaded baseline into the Start column so
    # the SP diff shows what the loop is actually starting from (not just
    # schema pipeline params). Otherwise any candidate that overrides a
    # prompt field would render as a diff against "-".
    prompt_fields = loop_state.opt_sp.prompt_field_dict()
    for field_name, value in prompt_fields.items():
        if value:
            state.original_sp_flat[field_name] = str(value)

    cycle_id = (loop_env.cycle_id or "?")[:12]
    samples = len(loop_env.scoring_dataset)
    obs = "ON" if (loop_env.scoring_ctx and loop_env.scoring_ctx.obs) else "OFF"
    print(
        f"  {GREEN}\u2713{RESET} Initialized  baseline={loop_state.current_accuracy:.1%}  "
        f"cycle={cycle_id}  samples={samples}  obs={obs}"
    )
    crit = loop_state.opt_sp.memory.critique_text
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"    {CYAN}Bootstrap critique:{RESET} {preview}")
    resumed = loop_env.resumed_from_round
    if resumed > 0:
        state_parts = []
        critique_chars = len(loop_state.opt_sp.memory.critique_text)
        task_context_keys = len(loop_state.opt_sp.task_context)
        l2_round = loop_state.escalation.l2.round
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
        print(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}  best candidate {w_acc:.1%}{comp_tag}")

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
        print(f"    {YELLOW}⚠ {warned} queries with recurring pipeline warnings ({top_w}){RESET}")

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


# ---------------------------------------------------------------------------
# Phase dispatch
# ---------------------------------------------------------------------------

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
