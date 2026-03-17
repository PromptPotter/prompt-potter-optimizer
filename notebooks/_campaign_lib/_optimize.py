"""Optimization rounds, feedback cycle, and Langfuse integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore

from ._display import (
    BOLD, CYAN, GREEN, MAGENTA, RED, RESET, YELLOW,
    _box_bottom, _box_line, _box_top,
    _dbox_bottom, _dbox_line, _dbox_sep, _dbox_top,
    _dotted_line, _fmt_delta, _fmt_query_result, _scoreboard,
    display_progress,
)
from ._stats import (
    fmt_ci, fmt_pvalue, min_detectable_effect, proportion_test, wilson_ci,
)

# Phase event printer for feedback cycle observability
from api.models.phase_event import PhaseEvent


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
    best_in_round: tuple[str, float] | None = None  # (label, accuracy)
    scan_context: dict | None = None  # cached for scan reasoning display
    candidates_meta: list = field(default_factory=list)  # from l1_generate exit

__all__ = [
    # Campaign
    "show_feedback_preflight", "run_feedback_cycle_notebook",
    "display_progress", "run_manual_round",
    "list_campaigns", "diff_campaign_config", "show_experiment_dashboard",
    # Langfuse
    "push_langfuse", "sync_langfuse",
]


# ---------------------------------------------------------------------------
# Optimization rounds (thin wrappers over prompt_optimizer)
# ---------------------------------------------------------------------------


async def run_manual_round(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    svc: dict,
) -> dict | None:
    """Run a single manual optimization round via feedback cycle.

    Returns:
        The round entry dict (also appended to campaign_rounds), or None
        if interrupted before any round completed.
    """
    # Override max_rounds=1 for single-round behaviour
    override = dict(campaign_config)
    override.setdefault("optimization", {})
    override["optimization"] = {**override["optimization"], "max_rounds": 1, "patience": 1}

    initial_len = len(campaign_rounds)

    await run_feedback_cycle_notebook(
        campaign_rounds, eval_data, override,
        store=svc.get("store"),
        backend_id=svc.get("backend_id", ""),
        backend_url=svc["backend_client"].base_url
        if svc.get("backend_client") else "http://127.0.0.1:8000",
        pipeline_params=campaign_config.get("pipeline_params"),
        session_terms=svc.get("session_terms"),
    )

    display_progress(campaign_rounds)

    if len(campaign_rounds) > initial_len:
        return campaign_rounds[-1]
    return None


# ---------------------------------------------------------------------------
# Feedback cycle (M3 node-based optimization)
# ---------------------------------------------------------------------------


def _extract_campaign_baseline(campaign_rounds: list) -> dict:
    """Extract baseline prompt state, accuracy, and results from campaign rounds.

    Searches reversed rounds for the last with actual eval ``results``,
    then overrides the prompt_state with the tip (most recent round).

    Returns dict with keys: baseline_ps, baseline_acc, baseline_results, instruction.
    """
    baseline_ps = None
    baseline_acc = 0.0
    baseline_results = None
    instruction = ""
    if campaign_rounds:
        # Find the last round with actual eval results (for accuracy + results)
        last = campaign_rounds[-1]
        for rd in reversed(campaign_rounds):
            if rd.get("results"):
                last = rd
                break
        baseline_acc = last.get("accuracy", 0.0)
        baseline_results = last.get("results", [])
        # Use prompt_state from the tip (most recent round) — may differ from
        # the round with results (e.g. search winner with derived prompt)
        tip_ps = campaign_rounds[-1]["prompt_state"]
        baseline_ps = tip_ps.model_dump() if hasattr(tip_ps, "model_dump") else tip_ps
        instruction = tip_ps.instruction if hasattr(tip_ps, "instruction") else ""
    return {
        "baseline_ps": baseline_ps,
        "baseline_acc": baseline_acc,
        "baseline_results": baseline_results,
        "instruction": instruction,
    }


def show_feedback_preflight(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    *,
    pipeline_params: "dict | None" = None,
    scan_df=None,
    axis_profiles=None,
    scan_variants=None,
    difficulty_df=None,
) -> "dict | None":
    """Display a rich pre-flight walkthrough for the feedback cycle.

    Builds scan context from raw DataFrames when available, then prints
    three sections: configuration summary, round pipeline walkthrough,
    and scan context preview.

    Call this in a separate notebook cell before ``run_feedback_cycle_notebook()``
    so the user can review config before committing to a run.

    Returns:
        scan_context dict (or None) for passing to the run cell.
    """
    from api.services.campaign.models import CycleConfig

    # Build scan context from scan data when available
    scan_context = None
    if scan_df is not None and axis_profiles is not None and scan_variants is not None:
        from api.services.search import prepare_scan_context

        baseline_acc = 0.0
        if campaign_rounds:
            baseline_acc = campaign_rounds[-1].get("accuracy", 0.0)

        difficulty_summary = None
        if difficulty_df is not None and len(difficulty_df) > 0:
            difficulty_summary = difficulty_df["classification"].value_counts().to_dict()
            difficulty_summary["total"] = len(difficulty_df)

        scan_context = prepare_scan_context(
            scan_df, axis_profiles, scan_variants, baseline_acc,
            difficulty_summary=difficulty_summary,
        )

    config = CycleConfig.from_campaign_config(
        campaign_config, pipeline_params=pipeline_params,
        scan_context=scan_context,
    )

    bl = _extract_campaign_baseline(campaign_rounds)

    _print_preflight_sections(
        config, bl, eval_data,
        campaign_config=campaign_config,
        scan_context=scan_context,
    )

    return scan_context


def _print_preflight_sections(config, bl, eval_data,
                              *, campaign_config=None, scan_context=None):
    """Print three-section preflight walkthrough."""
    from api.services.prompt_optimizer import MAX_FAILURES_GENERATE

    baseline_acc = bl["baseline_acc"]
    instruction = bl["instruction"]
    baseline_results = bl["baseline_results"]

    _instr_preview = (instruction[:80] + "...") if len(instruction) > 80 else instruction
    _instr_preview = _instr_preview or "(empty)"
    _eff_queries = config.sample_size if config.sample_size else len(eval_data)
    if config.sample_size:
        _queries_label = f"{config.sample_size} of {len(eval_data)}"
    else:
        _queries_label = f"all {len(eval_data)}"
    # Pipeline step display
    pp = config.pipeline_params or {}
    active_steps = pp.get("steps", [])
    exclude = (campaign_config or {}).get("exclude_steps", [])
    total_steps = len(active_steps) + len(exclude)
    if active_steps:
        _pipeline_label = f"{len(active_steps)} of {total_steps} steps"
        _steps_detail = ", ".join(active_steps)
    else:
        _pipeline_label = "(default pipeline)"
        _steps_detail = None

    _est_calls = config.max_rounds * config.n_variants * _eff_queries

    _l2_label = (f"enabled, patience={config.l2_patience}"
                 if config.enable_l2 else "disabled")
    _l3_label = (f"enabled, patience={config.l3_patience}"
                 if config.enable_l3 else "disabled")

    _strategy = "SCAN-AWARE" if scan_context else "FREEFORM"

    # ── Section 1: Configuration Summary ──
    print()
    print("=" * 70)
    print(f"  {BOLD}FEEDBACK CYCLE PRE-FLIGHT{RESET}")
    print("=" * 70)
    print(f"  Baseline accuracy      : {baseline_acc:.1%}")
    print(f"  Baseline prompt        : {_instr_preview}")
    print("  " + "-" * 66)
    print(f"  Max rounds             : {config.max_rounds}")
    print(f"  Candidates per round   : {config.n_variants}")
    print(f"  Queries per eval       : {_queries_label}")
    print(f"  Improvement threshold  : {config.improvement_threshold:.1%}")
    print(f"  Patience (L1)          : {config.patience} rounds")
    print(f"  L2 (refine context)    : {_l2_label}")
    print(f"  L3 (modify plan)       : {_l3_label}")
    print("  " + "-" * 66)
    print(f"  Candidate model        : {config.model or '(default)'}")
    print(f"  Creativity             : {config.creativity}")
    print(f"  Pipeline               : {_pipeline_label}")
    if _steps_detail:
        print(f"    Steps                : {_steps_detail}")
    if exclude:
        print(f"    Excluded             : {', '.join(exclude)}")
    print(f"  Strategy               : {_strategy}")

    # ── Section 2: Round Pipeline ──
    print()
    print(f"  {BOLD}ROUND PIPELINE{RESET} (what happens each round)")
    print("  " + "-" * 66)

    # Step 1: Baseline input
    n_prior = len(baseline_results) if baseline_results else 0
    print(f"  {CYAN}1. BASELINE INPUT{RESET}")
    print(f"     Prompt: {_instr_preview}")
    print(f"     Accuracy: {baseline_acc:.1%}  |  Prior results: {n_prior}")

    # Step 2: Failure analysis
    n_failures = 0
    if baseline_results:
        n_failures = sum(1 for r in baseline_results if not r.get("hit"))
    print(f"  {CYAN}2. FAILURE ANALYSIS{RESET}")
    print(f"     Up to {MAX_FAILURES_GENERATE} failures extracted"
          f" (currently {n_failures} available)")

    # Step 3: Context assembly
    print(f"  {CYAN}3. CONTEXT ASSEMBLY{RESET}")
    print(f"     Strategy: {_strategy}")
    if scan_context:
        improving = scan_context.get("improving_axes", [])
        leaderboard = scan_context.get("leaderboard_text", "")
        n_leaderboard = leaderboard.count("\n") + 1 if leaderboard.strip() else 0
        tested = scan_context.get("tested_values", "")
        n_tested = sum(1 for line in tested.split("\n")
                       if line.strip() and "values tested" in line) if tested else 0
        sensitivity = scan_context.get("sensitivity_text", "")
        n_axes = sensitivity.count("\n") + 1 if sensitivity.strip() else 0
        difficulty = scan_context.get("difficulty_text", "")

        print(f"     Scan leaderboard: {n_leaderboard} entries")
        if n_leaderboard > 0:
            # Show top performer from leaderboard
            first_line = leaderboard.strip().split("\n")[0].strip()
            print(f"     Top performer: {first_line}")
        print(f"     Sensitivity axes: {n_axes}")
        print(f"     Difficulty: {difficulty.strip()}")
        print(f"     Improving axes: {len(improving)} ({', '.join(improving)})")
        print(f"     Tested values: {n_tested} axes with per-value data")
    else:
        print("     (no scan data — LLM generates from failures only)")

    # Step 4: LLM candidate generation
    print(f"  {CYAN}4. LLM CANDIDATE GENERATION{RESET}")
    print(f"     Model: {config.model or '(default)'}  |  "
          f"Temperature: {config.creativity}")
    print(f"     Candidates: {config.n_variants}")
    if scan_context:
        print("     Output: prompt + pipeline_params_override per candidate")
    else:
        print("     Output: prompt variants")

    # Step 5: Backend evaluation
    print(f"  {CYAN}5. BACKEND EVALUATION{RESET}")
    print(f"     Queries per candidate: {_queries_label}")
    scoring = "composite (pipeline-aware)" if config.pipeline_schema else "accuracy"
    print(f"     Scoring: {scoring}")

    # Step 6: Winner selection
    print(f"  {CYAN}6. WINNER SELECTION{RESET}")
    print(f"     Criterion: best accuracy >= baseline + "
          f"{config.improvement_threshold:.1%}")

    # Step 7: Loop control
    print(f"  {CYAN}7. LOOP CONTROL{RESET}")
    print(f"     Patience: {config.patience} stalls → stop  |  "
          f"L2: {_l2_label}  |  L3: {_l3_label}")

    print("  " + "-" * 66)
    print(f"  Est. backend calls     : {_est_calls}"
          f"  ({config.max_rounds}r x {config.n_variants}c"
          f" x {_eff_queries}q)")

    # ── Section 3: Scan Context Preview ──
    if scan_context:
        print()
        print(f"  {BOLD}SCAN CONTEXT PREVIEW{RESET}"
              " (injected into LLM meta-prompt)")
        print("  " + "-" * 66)

        # Leaderboard (top 10)
        leaderboard = scan_context.get("leaderboard_text", "")
        if leaderboard.strip():
            lines = leaderboard.strip().split("\n")
            print(f"  {CYAN}Leaderboard:{RESET}")
            for line in lines[:10]:
                print(f"  {line}")
            if len(lines) > 10:
                print(f"    ... {len(lines) - 10} more")

        # Axis sensitivity
        sensitivity = scan_context.get("sensitivity_text", "")
        if sensitivity.strip():
            print(f"  {CYAN}Axis sensitivity:{RESET}")
            for line in sensitivity.strip().split("\n"):
                print(f"  {line}")

        # Difficulty
        difficulty = scan_context.get("difficulty_text", "")
        if difficulty.strip():
            print(f"  {CYAN}Query difficulty:{RESET}")
            print(f"  {difficulty.strip()}")

        # Improving axes
        improving = scan_context.get("improving_axes", [])
        if improving:
            print(f"  {CYAN}Improving axes:{RESET} {', '.join(improving)}")

        # Tested values
        tested = scan_context.get("tested_values", "")
        if tested.strip():
            lines = tested.strip().split("\n")
            print(f"  {CYAN}Tested values:{RESET}")
            for line in lines[:15]:
                print(f"  {line}")
            if len(lines) > 15:
                print(f"    ... {len(lines) - 15} more lines")

    print("=" * 70)


# ---------------------------------------------------------------------------
# Phase-specific display formatters
# ---------------------------------------------------------------------------


def _print_init_enter(d: dict, state: _CycleDisplayState) -> None:
    state.max_rounds = d.get("max_rounds", 0)
    state.patience = d.get("patience", 0)
    state.baseline_accuracy = d.get("baseline_accuracy", 0.0)

    model = d.get("model", "(default)")
    l2 = "enabled" if d.get("enable_l2") else "disabled"
    l3 = "enabled" if d.get("enable_l3") else "disabled"
    scan = "YES" if d.get("has_scan_context") else "NO"

    print()
    print(_dbox_top())
    print(_dbox_line(f"{BOLD}FEEDBACK CYCLE STARTING{RESET}"))
    print(_dbox_sep())
    print(_dbox_line(
        f"Baseline       {d.get('baseline_accuracy', 0):.1%}"))
    print(_dbox_line(
        f"Max rounds     {state.max_rounds:<15d}"
        f"Patience    {state.patience}"))
    print(_dbox_line(
        f"Candidates     {d.get('n_variants', 0)}"))
    sample = d.get("sample_size", 0)
    total = d.get("eval_data_count", 0)
    eff_n = sample if sample else total
    state.baseline_total = eff_n
    sample_label = f"{sample} of {total}" if sample else f"all {total}"
    mde = min_detectable_effect(eff_n)
    print(_dbox_line(f"Sample size    {sample_label}"))
    print(_dbox_line(
        f"Min detectable {YELLOW}\u00b1{mde:.1%}{RESET} (\u03b1=0.05, 80% power)"))
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
    print(f"  {GREEN}✓{RESET} Initialized  cycle={cycle_id}"
          f"  samples={samples}  obs={obs}")
    crit = d.get("critique_text", "")
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."
        print(f"    {CYAN}Bootstrap critique:{RESET} {preview}")
    resumed = d.get("resumed_from_round", 0)
    if resumed > 0:
        print(f"    Resumed from round {resumed} ({resumed} rounds cached)")
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
    model = (d.get("model") or "(default)")
    creativity = d.get("creativity", 0.7)
    scan = "YES" if d.get("has_scan_context") else "NO"

    r_label = f"Round {state.round_num + 1}/{state.max_rounds}"
    p_label = f"patience {state.stall_count}/{state.patience}"

    print()
    print(_box_top(r_label, p_label))
    print(_box_line(f"{CYAN}{BOLD}GENERATING CANDIDATES{RESET}"))
    print(_box_line(f"Current best    {acc:.1%}"))
    print(_box_line(f"Prompt          {preview}"))
    crit = "YES" if d.get("has_critique") else "NO"
    print(_box_line(
        f"Candidates      {n}   Creativity: {creativity}   Scan: {scan}"
        f"   Critique: {crit}"))
    print(_box_line(f"Model           {model}"))
    print(_box_bottom())

    # Scan reasoning: show what the LLM was told to focus on
    if state.scan_context and d.get("has_scan_context"):
        axes = state.scan_context.get("improving_axes", [])
        bl_acc = state.scan_context.get("baseline_accuracy", 0.0)
        if axes:
            print(f"  {CYAN}Scan focus:{RESET} {len(axes)} improving axes"
                  f" [{', '.join(axes)}]")
        if bl_acc > 0:
            print(f"  {CYAN}Scan baseline:{RESET} {bl_acc:.1%}")


def _print_l1_generate_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_candidates", 0)
    source = "loaded from disk" if d.get("loaded_from_disk") else "from LLM"
    n_eval = d.get("n_eval_queries", 0)
    state.candidates_meta = d.get("candidates", [])

    print(f"  {GREEN}✓{RESET} {n} candidates generated ({source})")

    for c in d.get("candidates", []):
        idx = c.get("idx", 0)
        desc = c.get("changes_description", "") or "(no description)"
        if len(desc) > 50:
            desc = desc[:47] + "..."
        fields = c.get("changed_fields", [])
        fields_tag = f" [{', '.join(fields)}]" if fields else ""
        pp = c.get("pipeline_params_override")
        if pp:
            keys = list(pp.keys())
            if len(keys) > 3:
                pp_tag = f" pp=[{', '.join(keys[:3])}, +{len(keys) - 3}]"
            else:
                pp_tag = f" pp=[{', '.join(keys)}]"
        else:
            pp_tag = ""
        print(f"    C{int(idx) + 1}: {desc}{fields_tag}{pp_tag}")

    print(f"  Evaluating {n} x {n_eval} = {n * n_eval} backend calls")


def _print_l1_evaluate_enter(d: dict, state: _CycleDisplayState) -> None:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    best = d.get("current_best_accuracy", 0.0)
    thresh = d.get("improvement_threshold", 0.01)
    print(f"\n  {CYAN}{BOLD}EVALUATING{RESET}  {n_cand} candidates"
          f" x {n_q} queries  |  beat {best:.1%} + {thresh:.1%}")


def _print_l1_evaluate_exit(d: dict, state: _CycleDisplayState) -> None:
    w_acc = d.get("winner_accuracy", 0.0)
    w_comp = d.get("winner_composite")
    improved = d.get("improved", False)
    action = d.get("next_action", "?")
    scores = d.get("candidate_scores", [])

    # Normalize labels to C{i+1} for display (service uses candidate_id)
    for i, s in enumerate(scores):
        s["label"] = f"C{i + 1}"
    best_s = max(scores, key=lambda s: (
        s.get("composite", s["accuracy"]), s["accuracy"]
    )) if scores else {}
    winner = best_s.get("label", "?")

    # Scoreboard (with CI column)
    board = _scoreboard(scores, winner, state.baseline_accuracy)
    if board:
        print(board)

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
        print(f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
              f" (was {state.baseline_accuracy:.1%},"
              f" {_fmt_delta(delta)}){comp_tag}{sig_tag}"
              f"  ->  next: {action}")
        state.baseline_accuracy = w_acc
    else:
        print(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}"
              f"  best candidate {w_acc:.1%}{comp_tag}"
              f"  ->  patience {state.stall_count + 1}/{state.patience}")

    # Critique preview (fed forward to next l1_generate)
    crit = d.get("critique_text", "")
    if crit:
        preview = crit.replace("\n", " ").strip()
        if len(preview) > 90:
            preview = preview[:87] + "..."
        print(f"  {CYAN}Critique:{RESET} {preview}")


def _print_refine_enter(d: dict, state: _CycleDisplayState) -> None:
    l2r = d.get("l2_round", "?")
    stalls = d.get("stall_count", "?")
    acc = d.get("current_accuracy", 0.0)
    best = d.get("best_accuracy", 0.0)
    params = d.get("current_params", {})
    print(f"\n  {MAGENTA}{_dotted_line('L2 REFINE CONTEXT')}{RESET}")
    print(f"  L2 round {l2r}  |  L1 stalled {stalls} rounds"
          f"  |  acc={acc:.1%}  best={best:.1%}")
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
        print(f"  Current params: {params_str}")
    else:
        print("  Current params: (none)")
    print("  LLM analyzing failure patterns for context refinement...")


def _print_refine_exit(d: dict, state: _CycleDisplayState) -> None:
    n_changes = d.get("param_changes_count", 0)
    ctx = "context updated" if d.get("context_changed") else "context unchanged"
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} L2 decision: {n_changes} param changes, {ctx}")
    if desc:
        print(f"    {desc}")
    print(f"  {MAGENTA}{_dotted_line()}{RESET}")


def _print_plan_enter(d: dict, state: _CycleDisplayState) -> None:
    l3r = d.get("l3_round", "?")
    l2_stalls = d.get("l2_stall_count", "?")
    plan = d.get("current_plan_preview", "")
    if len(plan) > 55:
        plan = plan[:52] + "..."
    print(f"\n  {MAGENTA}{_dotted_line('L3 MODIFY PLAN')}{RESET}")
    print(f"  L3 round {l3r}  |  L2 stalled {l2_stalls} rounds")
    print(f"  Current plan: {plan}")
    print("  LLM designing new strategy...")


def _print_plan_exit(d: dict, state: _CycleDisplayState) -> None:
    new_plan = d.get("new_plan_preview", "")
    if len(new_plan) > 55:
        new_plan = new_plan[:52] + "..."
    desc = d.get("changes_description", "")
    print(f"  {GREEN}✓{RESET} New plan: {new_plan}")
    if desc:
        print(f"    {desc}")
    print(f"  {MAGENTA}{_dotted_line()}{RESET}")


# Phase dispatch table
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
        print(f"  [{event.phase.upper()} {event.event}]"
              f" {event.data}")


def _round_result_to_dict(rr, round_idx: int) -> dict:
    """Convert CycleRoundResult to campaign_rounds entry."""
    d = rr.model_dump()
    ps_raw = d.get("prompt_state", {})
    d["prompt_state"] = PromptState(**ps_raw) if isinstance(ps_raw, dict) else ps_raw
    d["round"] = round_idx
    return d


async def run_feedback_cycle_notebook(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    *,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    backend_url: str = "http://127.0.0.1:8000",
    pipeline_params: "dict | None" = None,
    session_terms: "list[str] | None" = None,
    langfuse_session_id: str | None = None,
    scan_context: "dict | None" = None,
) -> list:
    """Run optimization via feedback cycle with optional L2/L3 escalation.

    Accepts and returns the ``campaign_rounds`` list format for downstream
    notebook sections.

    When ``scan_context`` is provided (from ``show_feedback_preflight()``),
    runs in scan-aware mode with per-candidate pipeline_params overrides.

    Returns:
        Updated campaign_rounds list.
    """
    from api.services.campaign.models import CycleConfig
    from api.services.campaign.feedback_cycle import run_feedback_cycle

    config = CycleConfig.from_campaign_config(
        campaign_config,
        backend_url=backend_url,
        backend_id=backend_id,
        project_root=str(store.base_dir) if store else "",
        pipeline_params=pipeline_params,
        session_terms=session_terms,
        scan_context=scan_context,
    )

    bl = _extract_campaign_baseline(campaign_rounds)
    baseline_ps = bl["baseline_ps"]
    baseline_acc = bl["baseline_acc"]
    baseline_results = bl["baseline_results"]
    instruction = bl["instruction"]

    # --- Warn if scan context was lost (kernel restart) ---
    if scan_context is None and any(
        r.get("round") == "search" for r in campaign_rounds
    ):
        print(f"  {YELLOW}⚠ Scan context not available — running without scan data.{RESET}")
        print("    Run the preflight cell to rebuild scan_context from scan variables.")

    # --- Display state (shared across closures) ---
    initial_len = len(campaign_rounds)
    _ds = _CycleDisplayState(baseline_accuracy=baseline_acc)
    _ds.scan_context = scan_context
    _query_counter = [0]

    def _on_phase(event: PhaseEvent) -> None:
        _dispatch_phase(event, _ds)
        # On resume: clear stale optimization rounds before replay re-appends them
        if event.phase == "init" and event.event == "exit":
            resumed = event.data.get("resumed_from_round", 0)
            if resumed > 0:
                del campaign_rounds[initial_len:]

    def _on_query(cand_idx, n_cands, query_idx, n_queries, result):
        _query_counter[0] += 1
        is_cached = result.get("cached", False)
        prefix = f"  [{_query_counter[0]:>3d}] "
        print(
            _fmt_query_result(result, cached=is_cached, prefix=prefix),
            flush=True,
        )

    def _on_candidate(idx, total, scores):
        label = f"C{idx + 1}"
        w = 66

        acc = scores["accuracy"]
        hits = scores.get("hits", 0)
        n = scores.get("total", 0)
        comp = scores.get("composite")
        ci_lo, ci_hi = wilson_ci(hits, n)
        delta = acc - _ds.baseline_accuracy

        if (_ds.best_in_round is None or acc > _ds.best_in_round[1]):
            _ds.best_in_round = (label, acc)

        # Header: label + accuracy with CI
        acc_tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"
        print(f"\n  {_box_top(f'{label}/{total}', acc_tag, width=w)}")

        # Line 1: description + pp from l1_generate exit metadata
        meta = {}
        if idx < len(_ds.candidates_meta):
            meta = _ds.candidates_meta[idx]
        desc = (meta.get("changes_description") or "")
        if len(desc) > 45:
            desc = desc[:42] + "..."
        pp = meta.get("pipeline_params_override")
        if pp:
            keys = list(pp.keys())
            if len(keys) > 2:
                pp_str = f"pp=[{', '.join(keys[:2])}, +{len(keys) - 2}]"
            else:
                pp_str = f"pp=[{', '.join(keys)}]"
            desc = f"{desc}  {pp_str}" if desc else pp_str
        if desc:
            print(f"  {_box_line(desc, width=w)}")

        # Line 2: hits/total + composite + delta + degraded
        stats = f"{hits}/{n} hits"
        if comp is not None and comp != acc:
            stats += f"  composite={comp:.4f}"
        degraded = scores.get("degraded_queries", 0)
        if degraded:
            stats += f"  {YELLOW}\u26a0 {degraded}/{n} degraded{RESET}"
        stats += f"  vs baseline: {_fmt_delta(delta)}"
        print(f"  {_box_line(stats, width=w)}")

        # Line 3: running best
        if _ds.best_in_round:
            bl, ba = _ds.best_in_round
            best_str = f"best so far: {bl} {ba:.1%}"
            print(f"  {_box_line(best_str, width=w)}")

        print(f"  {_box_bottom(width=w)}")

    def _on_round(round_result, stall_count):
        _query_counter[0] = 0
        _ds.stall_count = stall_count
        _ds.best_in_round = None

        round_entry = _round_result_to_dict(round_result, len(campaign_rounds))
        campaign_rounds.append(round_entry)

        rn = _ds.round_num + 1
        print(f"\n├─ Round {rn} complete "
              f"{'─' * (70 - 20 - len(str(rn)))}┤")
        display_progress(campaign_rounds)
        print(f"  hits: {round_result.hits}/{round_result.total}"
              f"  |  evaluated: {round_result.candidates_evaluated} candidates")

        if round_result.improved:
            print(f"  {GREEN}✓ Improvement detected, auto-continuing...{RESET}")
        else:
            print(f"  {YELLOW}⚠ No improvement"
                  f" ({stall_count}/{config.patience} patience){RESET}")
            if stall_count >= config.patience:
                print(f"  {RED}Stopping: patience exhausted"
                      f" ({config.patience} consecutive stalls){RESET}")

    result = await run_feedback_cycle(
        instruction=instruction,
        eval_data=eval_data,
        config=config,
        baseline_prompt_state=baseline_ps,
        baseline_accuracy=baseline_acc,
        baseline_results=baseline_results,
        on_round_complete=_on_round,
        on_candidate_eval=_on_candidate,
        on_query_eval=_on_query,
        on_phase=_on_phase,
        langfuse_session_id=langfuse_session_id,
    )

    # --- Final summary ---
    if not campaign_rounds:
        print(f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
              f"stopped before any rounds completed.")
        print("  Resume: re-run this cell to restart.")
        return campaign_rounds

    best = max(campaign_rounds, key=lambda r: r["accuracy"])
    interrupted = result.stop_reason == "interrupted"
    title = (f"{YELLOW}{BOLD}INTERRUPTED{RESET} — stopped by user"
             if interrupted
             else f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}")

    print()
    print(_dbox_top())
    print(_dbox_line(title))
    print(_dbox_sep())
    print(_dbox_line(f"Rounds       {result.n_rounds:<15d}"
                     f"Best         {best['accuracy']:.1%}"
                     f" (round {best['round']})"))
    print(_dbox_line(f"Stop reason  {result.stop_reason}"))
    if interrupted:
        print(_dbox_line("Resume: re-run this cell -- rounds auto-restore"))
    if result.cycle_id:
        print(_dbox_line(f"Cycle ID     {result.cycle_id}"))
    if result.langfuse_trace_id:
        print(_dbox_line(f"Langfuse     {result.langfuse_trace_id}"))
    print(_dbox_bottom())

    return campaign_rounds


# ---------------------------------------------------------------------------
# Campaign listing
# ---------------------------------------------------------------------------


def list_campaigns(
    store: "ProjectStore",
    backend_id: str,
    *,
    campaign_id: str | None = None,
) -> list[dict] | dict | None:
    """Interactive campaign explorer — two modes in one cell.

    **Overview** (no campaign_id): table of all campaigns with inline config.
    **Detail** (campaign_id provided): full config for one campaign, copy-pasteable.

    Usage::

        # Mode 1: overview
        list_campaigns(store=svc["store"], backend_id=svc["backend_id"])

        # Mode 2: detail (copy an ID from the overview)
        list_campaigns(store=svc["store"], backend_id=svc["backend_id"],
                       campaign_id="cycle_68e2c53845c3")
    """
    import json as _json

    if campaign_id is not None:
        # --- Detail mode ---
        campaign = store.campaigns.load(backend_id, campaign_id)
        if campaign is None:
            print(f"Campaign {campaign_id} not found.")
            return None

        status = campaign["status"]
        n = campaign["n_trials"]
        best = f"{campaign['best_accuracy']:.1%}"
        base = f"{campaign['baseline_accuracy']:.1%}"
        updated = campaign["updated_at"][:16].replace("T", " ")

        print(f"\nCAMPAIGN: {campaign_id}")
        print("=" * 72)
        print(f"  Status: {status}  |  Rounds: {n}  |  Best: {best}  |  Baseline: {base}")
        print(f"  Updated: {updated}")

        cfg = campaign.get("config", {})
        if cfg:
            print(f"\n  Config (copy to campaign_config):")
            for k in ["max_rounds", "patience", "n_variants", "creativity",
                       "improvement_threshold", "model", "temperature",
                       "sample_size", "seed"]:
                if k in cfg:
                    print(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                pp_str = _json.dumps(pp, indent=6)
                print(f"    pipeline_params: {pp_str}")

        print("=" * 72)
        return campaign

    # --- Overview mode ---
    campaigns = store.campaigns.list_all(backend_id)

    if not campaigns:
        print("No campaigns found.")
        return []

    print(f"\nCAMPAIGNS ({backend_id})")
    print("=" * 72)
    for c in sorted(campaigns, key=lambda x: x["updated_at"], reverse=True):
        cid = c["campaign_id"]
        status = c["status"]
        n = c["n_trials"]
        best = f"{c['best_accuracy']:.1%}"
        base = f"{c['baseline_accuracy']:.1%}"
        updated = c["updated_at"][:16].replace("T", " ")

        print(f"  {cid}  {status}  {n} rounds  best={best}  base={base}")

        # Inline config from stored campaign data
        full = store.campaigns.load(backend_id, cid)
        cfg = full.get("config", {}) if full else {}
        if cfg:
            model = cfg.get("model", "?")
            patience = cfg.get("patience", "?")
            max_r = cfg.get("max_rounds", "?")
            sample = cfg.get("sample_size", "?")
            print(f"    model={model}  patience={patience}  "
                  f"rounds={max_r}  sample={sample}")
        print(f"    updated: {updated}")
        print()

    print("=" * 72)
    print(f'{len(campaigns)} campaign(s) — pass campaign_id="cycle_..." to see full config\n')
    return campaigns


def diff_campaign_config(
    store: "ProjectStore",
    backend_id: str,
    campaign_id: str,
    campaign_config: dict,
    pipeline_params: dict | None = None,
) -> dict:
    """Show parameter differences between current config and a stored campaign.

    Usage::

        diff_campaign_config(svc["store"], svc["backend_id"],
                             "cycle_68e2c53845c3", campaign_config,
                             pipeline_params=campaign_config.get("pipeline_params"))
    """
    from api.services.campaign.models import CycleConfig

    campaign = store.campaigns.load(backend_id, campaign_id)
    if campaign is None:
        print(f"Campaign {campaign_id} not found.")
        return {}

    stored = campaign.get("config", {})
    current = CycleConfig.from_campaign_config(
        campaign_config, pipeline_params=pipeline_params,
    ).model_dump()

    keys = ["max_rounds", "patience", "n_variants", "creativity",
            "improvement_threshold", "model", "temperature",
            "sample_size", "seed"]

    diffs: dict = {}
    print(f"\nDiff: current config vs {campaign_id}")
    print("=" * 60)
    any_diff = False
    for k in keys:
        sv = stored.get(k)
        cv = current.get(k)
        if sv != cv:
            print(f"  {k}: {sv} (stored) → {cv} (current)")
            diffs[k] = {"stored": sv, "current": cv}
            any_diff = True

    # Pipeline params diff
    sp = stored.get("pipeline_params")
    cp = current.get("pipeline_params")
    if sp != cp:
        sp_keys = set(sp or {})
        cp_keys = set(cp or {})
        for pk in sorted(sp_keys | cp_keys):
            sv = (sp or {}).get(pk)
            cv = (cp or {}).get(pk)
            if sv != cv:
                sv_str = str(sv)[:40] if sv is not None else "(none)"
                cv_str = str(cv)[:40] if cv is not None else "(none)"
                print(f"  pp.{pk}: {sv_str} → {cv_str}")
                diffs[f"pp.{pk}"] = {"stored": sv, "current": cv}
                any_diff = True

    if not any_diff:
        print("  (identical — will resume this campaign)")
    print("=" * 60)
    return diffs


def _resolve_experiment_id(store: "ProjectStore", backend_id: str, short_id: str) -> str | None:
    """Resolve short prefix/suffix to full campaign_id."""
    campaigns = store.campaigns.list_all(backend_id)
    matches = [c for c in campaigns
               if short_id in c["campaign_id"]]
    if len(matches) == 1:
        return matches[0]["campaign_id"]
    if len(matches) > 1:
        print(f"  Ambiguous ID '{short_id}' — matches:")
        for m in matches:
            print(f"    {m['campaign_id']}  {m['status']}  {m['n_trials']} rounds")
        return None
    print(f"  No campaign matching '{short_id}'")
    return None


def show_experiment_dashboard(
    store: "ProjectStore",
    backend_id: str,
    *,
    experiment_id: str | None = None,
    campaign_config: dict | None = None,
    eval_data: list | None = None,
    pipeline_params: dict | None = None,
    baseline_prompt_state: dict | None = None,
) -> dict | list | None:
    """Unified experiment dashboard — overview or detail by experiment ID.

    **Overview** (experiment_id=None): all campaigns + data summary + active detection.
    **Detail** (experiment_id="68e2c5"): full config + diff vs current.
    """
    import json as _json

    # --- Resolve short ID ---
    full_id = None
    if experiment_id is not None:
        full_id = _resolve_experiment_id(store, backend_id, experiment_id)
        if full_id is None:
            return None

    # --- Detect active campaign from current config ---
    active_id = None
    if campaign_config is not None and eval_data is not None:
        try:
            from api.services.campaign.feedback_cycle import cycle_config_identity
            from api.services.campaign.models import CycleConfig

            config = CycleConfig.from_campaign_config(
                campaign_config, pipeline_params=pipeline_params,
            )
            bl_rendered = ""
            if baseline_prompt_state:
                bl_rendered = PromptState(**baseline_prompt_state).render()
            active_id = cycle_config_identity(config, bl_rendered, eval_data)
        except Exception:
            pass

    # --- Detail mode ---
    if full_id is not None:
        campaign = store.campaigns.load(backend_id, full_id)
        if campaign is None:
            print(f"Campaign {full_id} not found.")
            return None

        status = campaign["status"]
        n = campaign["n_trials"]
        best = f"{campaign['best_accuracy']:.1%}"
        base = f"{campaign['baseline_accuracy']:.1%}"
        updated = campaign["updated_at"][:16].replace("T", " ")

        print(f"\n{'=' * 72}")
        print(f"  EXPERIMENT: {full_id}")
        print(f"  Status: {status}  |  Rounds: {n}  |  Best: {best}  |  Base: {base}")
        print(f"  Updated: {updated}")

        cfg = campaign.get("config", {})
        if cfg:
            print(f"\n  Config (copy to campaign_config to resume):")
            for k in ["max_rounds", "patience", "n_variants", "creativity",
                       "improvement_threshold", "model", "temperature",
                       "sample_size", "seed"]:
                if k in cfg:
                    print(f"    {k}: {cfg[k]}")
            pp = cfg.get("pipeline_params")
            if pp:
                # Show keys only, not full nested dicts
                pp_keys = sorted(pp.keys())
                pp_summary = ", ".join(
                    f"{k}={str(pp[k])[:25]}" if not isinstance(pp[k], (dict, list))
                    else f"{k}=..."
                    for k in pp_keys[:6]
                )
                if len(pp_keys) > 6:
                    pp_summary += f" +{len(pp_keys) - 6} more"
                print(f"    pipeline_params: {{{pp_summary}}}")

        # Config diff
        if campaign_config is not None:
            print()
            diff_campaign_config(
                store, backend_id, full_id, campaign_config,
                pipeline_params=pipeline_params,
            )

        is_active = full_id == active_id
        if is_active:
            if status == "completed" and n > 0:
                print(f"  → Re-run will REPLAY from cache (campaign completed)")
            elif n > 0:
                print(f"  → Re-run will RESUME from round {n}")
            else:
                print(f"  → Re-run starts fresh")
        else:
            print(f"  → Config does NOT match — update campaign_config to resume")

        print(f"{'=' * 72}\n")
        return campaign

    # --- Overview mode ---
    # Dataset runs summary
    runs = store.dataset_runs.list_all(backend_id)
    by_source: dict[str, int] = {}
    best_acc = 0.0
    best_name = ""
    for r in runs:
        name = r.get("name", "")
        source = name.split("_")[0] if "_" in name else "other"
        by_source[source] = by_source.get(source, 0) + 1
        acc = r.get("scores", {}).get("accuracy", 0.0) or 0.0
        if acc > best_acc:
            best_acc = acc
            best_name = name

    source_str = ", ".join(f"{v} {k}" for k, v in sorted(by_source.items()))

    # Campaigns
    campaigns = store.campaigns.list_all(backend_id)

    print(f"\n{'=' * 72}")
    print(f"  EXPERIMENT DASHBOARD ({backend_id})")
    print(f"{'=' * 72}")
    if runs:
        print(f"  Dataset runs: {len(runs)} total ({source_str})")
        if best_acc > 0:
            print(f"  Best result: {best_acc:.1%} ({best_name})")
    else:
        print(f"  Dataset runs: none")

    if not campaigns:
        print(f"\n  No campaigns yet.")
    else:
        print(f"\n  Campaigns (most recent first):")
        sorted_camps = sorted(campaigns, key=lambda x: x["updated_at"], reverse=True)
        for i, c in enumerate(sorted_camps):
            cid = c["campaign_id"]
            short = cid.replace("cycle_", "")[:12]
            status = c["status"]
            n = c["n_trials"]
            best = f"{c['best_accuracy']:.1%}"
            base = f"{c['baseline_accuracy']:.1%}"
            is_active = cid == active_id

            marker = "  ●" if is_active else "   "
            tag = "  <-- active" if is_active else ""
            print(f"{marker} {cid}  {status:<12} {n} rounds  "
                  f"best={best}  base={base}{tag}")

            # Inline config
            full = store.campaigns.load(backend_id, cid)
            cfg = full.get("config", {}) if full else {}
            if cfg:
                model = str(cfg.get("model", "?"))[:30]
                patience = cfg.get("patience", "?")
                max_r = cfg.get("max_rounds", "?")
                sample = cfg.get("sample_size", "?")
                print(f"      patience={patience}  rounds={max_r}  "
                      f"sample={sample}  model={model}")

            # Resume hint for active
            if is_active:
                ac = store.campaigns.load(backend_id, cid)
                ac_status = ac["status"] if ac else "?"
                ac_n = ac["n_trials"] if ac else 0
                if ac_status == "completed" and ac_n > 0:
                    print(f"      → Re-run will REPLAY from cache")
                elif ac_n > 0:
                    print(f"      → Re-run will RESUME from round {ac_n}")
                else:
                    print(f"      → Re-run starts fresh")
            print()

    print(f"{'=' * 72}")
    hint = 'Set experiment_id="<short_id>" to see full config and diff'
    print(f"  {hint}")
    if active_id:
        print(f"  Active: {active_id}")
    elif campaign_config is not None:
        print(f"  No matching campaign — feedback cycle will create new")
    print()
    return campaigns


# ---------------------------------------------------------------------------
# Langfuse push / configure
# ---------------------------------------------------------------------------


def configure_langfuse(
    *,
    enabled: bool | None = None,
    host: str | None = None,
    public_key: str | None = None,
    secret_key: str | None = None,
) -> None:
    """Configure Langfuse settings at runtime from a notebook cell.

    Mutates the global settings singleton and resets the LangfuseLogger
    singleton so the next call picks up new credentials.

    Args:
        enabled: Override ``LANGFUSE_ENABLED``.
        host: Override ``LANGFUSE_HOST``.
        public_key: Override ``LANGFUSE_PUBLIC_KEY``.
        secret_key: Override ``LANGFUSE_SECRET_KEY``.
    """
    from api.config.settings import settings
    from api.services.obs.langfuse_client import LangfuseLogger

    changed = False
    if enabled is not None:
        settings.LANGFUSE_ENABLED = enabled
        changed = True
    if host is not None:
        settings.LANGFUSE_HOST = host
        changed = True
    if public_key is not None:
        settings.LANGFUSE_PUBLIC_KEY = public_key
        changed = True
    if secret_key is not None:
        settings.LANGFUSE_SECRET_KEY = secret_key
        changed = True

    if changed:
        LangfuseLogger.reset_instance()
        lf = LangfuseLogger.get_instance()
        status = "enabled" if lf.enabled else "disabled"
        print(f"Langfuse reconfigured: {status}")


def sync_langfuse(
    store: "ProjectStore",
    backend_id: str,
    *,
    dataset_name: str = "termnorm_ground_truth",
    backfill: bool = True,
    reset: bool = False,
) -> dict | None:
    """Configure Langfuse dataset name and optionally push all runs.

    Combines dataset name configuration, state reset, and backfill push
    into a single call.

    Returns:
        Push stats dict, or None if backfill was skipped.
    """
    import api.services.obs.langfuse_push as _lfp
    _lfp.DATASET_NAME = dataset_name

    if not backfill:
        print(f"Langfuse dataset: {dataset_name} (backfill disabled)")
        return None

    if reset:
        from api.services.obs.langfuse_push import _fresh_state, _save_state
        _save_state(store, backend_id, _fresh_state())
        print("Langfuse push state reset -- will re-push all runs.")

    n_runs = len(store.dataset_runs.list_all(backend_id))
    if n_runs == 0:
        print("No completed dataset runs yet -- skipping Langfuse backfill (run after eval).")
        return None

    return push_langfuse(store, backend_id)


def push_langfuse(store: "ProjectStore", backend_id: str) -> dict:
    """Push all historical dataset_runs to cloud Langfuse (dataset-first).

    Creates dataset items with ground truth, then one trace per run linked
    to dataset items. Re-running is safe -- already-pushed runs are skipped.
    Old-format state is automatically reset.

    Returns:
        Stats dict from ``push_all_runs()``.
    """
    from api.services.obs.langfuse_push import push_all_runs

    summaries = store.dataset_runs.list_all(backend_id)

    print("=" * 70)
    print("  LANGFUSE PUSH (dataset-first)")
    print("=" * 70)
    print(f"Found {len(summaries)} completed dataset runs for '{backend_id}'")

    stats = push_all_runs(store, backend_id, on_progress=print)

    if "error" in stats:
        print(f"\nPush aborted: {stats['error']}")
        return stats

    new_runs = stats["new_runs"]
    already = stats["already_done"]

    if new_runs == 0:
        print(f"\nAll {already} runs already pushed. Nothing to do.")
    else:
        print(f"\nPush complete: {new_runs} runs pushed to Langfuse")
        print(f"Session: {stats['session_id']}")

    print("=" * 70)
    print("  PUSH SUMMARY")
    print("=" * 70)
    print(f"  Total runs on disk:  {stats['total_on_disk']}")
    print(f"  Newly pushed:        {new_runs}")
    print(f"  Already done:        {already}")
    print(f"  Dataset:             {stats.get('dataset_name', 'N/A')}")
    print(f"  Dataset items:       {stats.get('dataset_items', 0)}")

    for origin, info in stats.get("origins", {}).items():
        n = info["n_runs"]
        items = info["total_items"]
        best = info["best_accuracy"]
        avg = info["avg_accuracy"]
        print(f"\n  {origin}:")
        print(f"    Runs: {n}, Items: {items}")
        print(f"    Best accuracy: {best:.1%}, Avg: {avg:.1%}")

    print("=" * 70)

    return stats
