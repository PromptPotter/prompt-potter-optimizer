"""Phase-specific display formatters for feedback cycle observability.

Unified visual system with three weight levels:
- CYCLE boundaries: double-line box (╔═══╗) for cycle start/end
- ROUND boundaries: heavy rule (━━━) between rounds
- NODE transitions: ├─ NODE ─┤ entry frame with │ content lines
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from promptpotter.services.campaign.state import PhaseEvent

if TYPE_CHECKING:
    from promptpotter.services.search.scan_results import ScanContext

from promptpotter.services.search.cohort_analysis import (
    min_detectable_effect,
    proportion_test,
)

from .display import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    NODE_FRAME_WIDTH,
    RED,
    RESET,
    YELLOW,
    _dbox_bottom,
    _dbox_line,
    _dbox_sep,
    _dbox_top,
    _fmt_delta,
    _scoreboard,
    fmt_pvalue,
)

# ---------------------------------------------------------------------------
# Display state — tracks cycle metadata across callbacks (display-only)
# ---------------------------------------------------------------------------


@dataclass
class _CycleDisplayState:
    """Mutable display state threaded through phase/callback closures.

    Populated exclusively from PhaseEvent data — never touches services.
    """

    max_rounds: int = 0
    patience: int = 0
    stall_count: int = 0
    round_num: int = 0
    baseline_accuracy: float = 0.0
    baseline_total: int = 0  # sample count for significance tests
    scan_context: ScanContext | None = None  # cached for scan reasoning display
    candidates_meta: list = field(default_factory=list)  # from l1_generate exit
    n_eval_queries: int = 0  # from generate:exit, used in evaluate:enter banner
    current_pipeline_params: dict | None = None  # raw pp for candidate eval callback
    # 3-column SP diff tracking (flattened dot-notation dicts)
    original_sp_flat: dict[str, str] = field(default_factory=dict)
    previous_sp_flat: dict[str, str] = field(default_factory=dict)
    current_sp_flat: dict[str, str] = field(default_factory=dict)
    # Pipeline node ordering for grouped diff display
    node_param_keys: dict[str, list[str]] | None = None


# ---------------------------------------------------------------------------
# Node-frame box-drawing helpers
# ---------------------------------------------------------------------------

_NW = NODE_FRAME_WIDTH


def _node_top(label: str, label_right: str = "", width: int = _NW) -> str:
    """Node entry banner: ``├─ LABEL ──────── label_right ─┤``."""
    inner = width - 4
    left = f" {label} " if label else ""
    right = f" {label_right} " if label_right else ""
    fill = inner - len(left) - len(right)
    return f"├─{left}{'─' * max(fill, 1)}{right}─┤"


def _node_bottom(width: int = _NW) -> str:
    """Node closing rule: ``├──────...──────┤``."""
    return f"├{'─' * (width - 2)}┤"


def _node_line(text: str) -> str:
    """Indented node content: ``│  text``."""
    return f"│  {text}"


def _round_rule(label: str, label_right: str = "", width: int = _NW) -> str:
    """Heavy round separator with labels.

    Returns a 3-line string: heavy rule, label line, heavy rule.
    """
    rule = "━" * width
    inner = f"  {label}"
    if label_right:
        pad = width - len(inner) - len(label_right) - 2
        inner = f"{inner}{' ' * max(pad, 2)}{label_right}"
    return f"{rule}\n{inner}\n{rule}"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _pp_val(v) -> str:
    """Format a pipeline param value for display.

    No truncation here — the diff table legend shows full values,
    and inline cells use _VAL_INLINE_MAX to decide when to use a
    legend code instead.
    """
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, (dict, list)):
        return str(v)
    return str(v)


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
            continue  # skip pipeline node list
        # JSON Schema object — drill into properties
        if isinstance(v, dict) and v.get("type") == "object" and "properties" in v:
            for prop_name, prop_def in v["properties"].items():
                desc = prop_def.get("description", prop_def.get("type", "?"))
                flat[f"{k}.{prop_name}"] = desc
        # Mutation tuple list: [['+', name, type, req, desc], ...]
        elif isinstance(v, list) and v and isinstance(v[0], list):
            for mutation in v:
                if not mutation:
                    continue
                op = mutation[0]
                if op == "+" and len(mutation) >= 5:
                    flat[f"{k}.{mutation[1]}"] = mutation[4]
                elif op == "~" and len(mutation) >= 6:
                    flat[f"{k}.{mutation[2]}"] = mutation[5]
                # '-' removals: absent = not in dict (handled by diff)
        # Plain nested dict (e.g. node overrides) — un-nest to flat param keys
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

    When a candidate overrides a schema key (e.g. profiling_schema),
    all parent's dot-notation children for that key are removed first,
    then the candidate's expanded fields are added.
    """
    flat = parent.copy()
    pp = candidate_meta.get("pipeline_params_override")
    if pp:
        # Remove parent's dot-children for any overridden schema keys
        for k in pp:
            prefix = f"{k}."
            to_remove = [pk for pk in flat if pk.startswith(prefix)]
            for pk in to_remove:
                del flat[pk]
        override_flat = _flatten_sp_summary(pp)
        flat.update(override_flat)
    return flat


def _group_diff_keys(
    diff_keys: list[str],
    node_param_keys: dict[str, list[str]] | None,
) -> list[tuple[str, list[str]]]:
    """Group diff keys by pipeline node in execution order.

    Returns ``(node_name, [keys])`` pairs.  When ``node_param_keys``
    is ``None``, returns a single unnamed group sorted alphabetically.
    """
    if not node_param_keys:
        return [("", diff_keys)]

    # Reverse map: flat_key → node_name (including dot-notation children)
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

    ``columns`` is a list of (label, flat_dict) pairs.
    Only rows where at least one column differs are shown.
    Short values (<=12 chars) are shown inline; longer values get a
    letter code [a]..[z] with full text in a legend below the table.

    When ``node_param_keys`` is provided, rows are grouped by pipeline
    node in execution order with separator lines between groups.
    """
    if len(columns) < 2:
        return

    # Collect all keys, filter to those that differ
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

    # Build lookup table: value → code letter
    lookup: dict[str, str] = {}  # long_value → "[a]"
    legend: list[tuple[str, str]] = []  # (code, full_value)
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

    # Column layout
    col_w = max(8, max(len(label) for label, _ in columns) + 2)
    max_key = max(len(k) for k in diff_keys)

    # Header
    r_label = f"Round {round_num}" if round_num is not None else "SPs"
    print(_node_line(f"{CYAN}{r_label} SPs:{RESET}"))
    hdr = f"{'':>{max_key}}  " + "".join(f"{label:<{col_w}}" for label, _ in columns)
    print(_node_line(hdr))

    # Rows — grouped by pipeline node
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

    # Legend
    if legend:
        print(_node_line(""))
        print(_node_line(f"{CYAN}Values:{RESET}"))
        for code, full in legend:
            print(_node_line(f"  {code} {full}"))


# ---------------------------------------------------------------------------
# Phase handlers — one per (phase, event) pair
# ---------------------------------------------------------------------------


def _print_init_enter(d: dict, state: _CycleDisplayState) -> None:
    state.max_rounds = d.get("max_rounds", 0)
    state.patience = d.get("patience", 0)
    state.baseline_accuracy = d.get("baseline_accuracy", 0.0)
    state.original_sp_flat = _flatten_sp_summary(
        d.get("pipeline_params"),
    )
    state.node_param_keys = d.get("node_param_keys")

    model = d.get("model", "(default)")
    l2 = "enabled" if d.get("enable_l2") else "disabled"
    l3 = "enabled" if d.get("enable_l3") else "disabled"
    scan = "YES" if d.get("has_scan_context") else "NO"

    print()
    print(_dbox_top())
    print(_dbox_line(f"{BOLD}FEEDBACK CYCLE STARTING{RESET}"))
    print(_dbox_sep())
    print(_dbox_line(f"Baseline       {d.get('baseline_accuracy', 0):.1%}"))
    print(
        _dbox_line(f"Max rounds     {state.max_rounds or 999!s:<15s}Patience    {state.patience}")
    )
    print(_dbox_line(f"Candidates     {d.get('n_variants', 0)}"))
    sample = d.get("sample_size", 0)
    total = d.get("dataset_count", 0)
    eff_n = sample if sample else total
    state.baseline_total = eff_n
    sample_label = f"{sample} of {total}" if sample else f"all {total}"
    mde = min_detectable_effect(eff_n)
    print(_dbox_line(f"Sample size    {sample_label}"))
    print(_dbox_line(f"Min detectable {YELLOW}\u00b1{mde:.1%}{RESET} (\u03b1=0.05, 80% power)"))
    print(_dbox_line(f"Model          {model}"))
    print(_dbox_line(f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}"))
    print(_dbox_line(f"Scan context   {scan}"))
    critique = "enabled" if d.get("enable_critique") else "disabled"
    print(_dbox_line(f"Critique       {critique}"))
    print(_dbox_bottom())


def _print_init_exit(d: dict, state: _CycleDisplayState) -> None:
    cycle_id = d.get("cycle_id", "?")[:12]
    samples = d.get("sample_count", "?")
    obs = "ON" if d.get("obs_enabled") else "OFF"
    print(f"  {GREEN}✓{RESET} Initialized  cycle={cycle_id}  samples={samples}  obs={obs}")
    crit = d.get("critique_text", "")
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"    {CYAN}Bootstrap critique:{RESET} {preview}")
    resumed = d.get("resumed_from_round", 0)
    if resumed > 0:
        rs = d.get("restored_state", {})
        state_parts = []
        if rs.get("critique_chars"):
            state_parts.append(f"critique={rs['critique_chars']} chars")
        if rs.get("task_context_keys"):
            state_parts.append(f"task_context={rs['task_context_keys']} keys")
        if rs.get("l2_round"):
            state_parts.append(f"l2_round={rs['l2_round']}")
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
        preview = "(no baseline -- seed from scan or provide instruction)"
    n = d.get("n_variants", 0)
    model = d.get("model") or "(default)"
    creativity = d.get("creativity", 0.7)
    scan = "YES" if d.get("has_scan_context") else "NO"
    crit = "YES" if d.get("has_critique") else "NO"

    # Rotate SP diff state
    new_flat = _flatten_sp_summary(
        d.get("pipeline_params"),
    )
    if state.round_num == 0:
        state.previous_sp_flat = state.original_sp_flat.copy()
    else:
        state.previous_sp_flat = state.current_sp_flat.copy()
    state.current_sp_flat = new_flat

    r_label = f"ROUND {state.round_num + 1}/{state.max_rounds or 999}"
    p_label = f"patience {state.stall_count}/{state.patience}"

    # Heavy round separator
    print()
    print(_round_rule(r_label, p_label))

    # Generate node frame
    print()
    print(_node_top("GENERATE"))
    print(_node_line(f"Current best    {acc:.1%}"))
    print(_node_line(f"Prompt          {preview}"))
    print(
        _node_line(
            f"Candidates      {n}   Creativity: {creativity}   Scan: {scan}   Critique: {crit}"
        )
    )
    print(_node_line(f"Model           {model}"))

    # Scan reasoning inside node frame
    if state.scan_context and d.get("has_scan_context"):
        axes = state.scan_context.improving_axes
        bl_acc = state.scan_context.baseline_accuracy
        if axes:
            print(
                _node_line(
                    f"{CYAN}Scan focus:{RESET} {len(axes)} improving axes [{', '.join(axes)}]"
                )
            )
        if bl_acc > 0:
            print(_node_line(f"{CYAN}Scan baseline:{RESET} {bl_acc:.1%}"))

    print(_node_bottom())


def _print_l1_generate_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_candidates", 0)
    source = "loaded from disk" if d.get("loaded_from_disk") else "from LLM"
    state.n_eval_queries = d.get("n_eval_queries", 0)
    state.candidates_meta = d.get("candidates", [])

    print(f"  {GREEN}✓{RESET} {n} candidates generated ({source})")

    # Multi-column SP diff: Start | Parent | C1..CN
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


def _print_l1_evaluate_enter(d: dict, state: _CycleDisplayState) -> None:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    n_calls = n_cand * n_q
    calls_label = f"{n_cand} \u00d7 {n_q} = {n_calls} calls"

    # Raw pp for candidate eval callback (don't touch sp_flat — GENERATE owns it)
    eval_pp = d.get("current_pipeline_params")
    if eval_pp is not None:
        state.current_pipeline_params = eval_pp

    print()
    print(_node_top("EVALUATE", calls_label))


def _print_l1_evaluate_exit(d: dict, state: _CycleDisplayState) -> None:
    w_acc = d.get("winner_accuracy", 0.0)
    w_comp = d.get("winner_composite")
    improved = d.get("improved", False)
    action = d.get("next_action", "?")
    scores = d.get("candidate_scores", [])

    # Normalize labels to C{i+1} for display (service uses candidate_id)
    for i, s in enumerate(scores):
        s["label"] = f"C{i + 1}"
    non_aborted = [s for s in scores if not s.get("escalation_aborted")]
    best_s = (
        max(non_aborted, key=lambda s: (s.get("composite", s["accuracy"]), s["accuracy"]))
        if non_aborted
        else {}
    )
    winner = best_s.get("label", "?")

    # Scoreboard: full table for >3 candidates, compact 1-liner otherwise
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

    # Composite suffix (only when it differs from accuracy)
    comp_tag = ""
    if w_comp is not None and w_comp != w_acc:
        comp_tag = f"  composite={w_comp:.4f}"

    # Significance test
    winner_hits = best_s.get("hits", 0)
    winner_total = best_s.get("total", 0)

    # Result line
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

    # Critique (fed forward to next l1_generate)
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
    stalls = d.get("stall_count", "?")
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

    # Warning inventory one-liner
    warned = d.get("warned_queries", 0)
    top_w = d.get("top_warning", "")
    if warned:
        print(f"    {YELLOW}⚠ {warned} queries with recurring pipeline warnings ({top_w}){RESET}")

    # Debug: show full L2 prompt and response
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

_PHASE_HANDLERS: dict[str, callable] = {
    "init:enter": _print_init_enter,
    "init:exit": _print_init_exit,
    "l1_generate:enter": _print_l1_generate_enter,
    "l1_generate:exit": _print_l1_generate_exit,
    "l1_evaluate:enter": _print_l1_evaluate_enter,
    "l1_evaluate:exit": _print_l1_evaluate_exit,
    "refine_context:enter": _print_refine_enter,
    "refine_context:exit": _print_refine_exit,
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
        # Fallback for unknown phases
        print(f"  [{event.phase.upper()} {event.event}] {event.data}")
