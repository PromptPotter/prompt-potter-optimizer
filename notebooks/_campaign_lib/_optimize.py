"""Optimization rounds, feedback cycle, and Langfuse integration."""

from __future__ import annotations

from dataclasses import dataclass
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
    best_in_round: tuple[str, float] | None = None  # (label, accuracy)
    scan_context: dict | None = None  # cached for scan reasoning display

__all__ = [
    # Campaign
    "show_feedback_preflight", "run_feedback_cycle_notebook",
    "display_progress", "run_manual_round",
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
        last = campaign_rounds[-1]
        for rd in reversed(campaign_rounds):
            if rd.get("results"):
                last = rd
                break
        ps = last["prompt_state"]
        baseline_ps = ps.model_dump() if hasattr(ps, "model_dump") else ps
        baseline_acc = last.get("accuracy", 0.0)
        baseline_results = last.get("results", [])
        instruction = ps.instruction if hasattr(ps, "instruction") else ""
        # Use prompt_state from the most recent round (may differ from
        # the round with results -- e.g. search winner with derived prompt)
        tip_ps = campaign_rounds[-1]["prompt_state"]
        baseline_ps = tip_ps.model_dump() if hasattr(tip_ps, "model_dump") else tip_ps
        instruction = tip_ps.instruction if hasattr(tip_ps, "instruction") else instruction
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
    from api.services.campaign.feedback_cycle import CycleConfig

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
    sample_label = f"{sample} of {total}" if sample else f"all {total}"
    print(_dbox_line(f"Sample size    {sample_label}"))
    print(_dbox_line(f"Model          {model}"))
    print(_dbox_line(f"L2 (refine)    {l2:<19s}L3 (plan)   {l3}"))
    print(_dbox_line(f"Scan context   {scan}"))
    print(_dbox_bottom())


def _print_init_exit(d: dict, state: _CycleDisplayState) -> None:
    cycle_id = d.get("cycle_id", "?")[:12]
    samples = d.get("sample_count", "?")
    obs = "ON" if d.get("obs_enabled") else "OFF"
    print(f"  {GREEN}✓{RESET} Initialized  cycle={cycle_id}"
          f"  samples={samples}  obs={obs}")
    resumed = d.get("resumed_from_round", 0)
    if resumed > 0:
        print(f"    Resumed from round {resumed} ({resumed} rounds cached)")
    else:
        print("    Starting fresh (no prior rounds for this cycle)")


def _print_growth_enter(d: dict, state: _CycleDisplayState) -> None:
    acc = d.get("current_accuracy", 0.0)
    preview = d.get("prompt_preview", "").replace("\n", " ").strip()
    if len(preview) > 55:
        preview = preview[:52] + "..."
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
    print(_box_line(
        f"Candidates      {n}   Creativity: {creativity}   Scan: {scan}"))
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


def _print_growth_exit(d: dict, state: _CycleDisplayState) -> None:
    n = d.get("n_candidates", 0)
    source = "loaded from disk" if d.get("loaded_from_disk") else "from LLM"
    n_eval = d.get("n_eval_queries", 0)

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
            pp_tag = f" pp=[{', '.join(keys)}]"
        else:
            pp_tag = ""
        print(f"    C{int(idx) + 1}: {desc}{fields_tag}{pp_tag}")

    print(f"  Evaluating {n} x {n_eval} = {n * n_eval} backend calls")


def _print_eval_enter(d: dict, state: _CycleDisplayState) -> None:
    n_cand = d.get("n_candidates", 0)
    n_q = d.get("n_queries", 0)
    best = d.get("current_best_accuracy", 0.0)
    thresh = d.get("improvement_threshold", 0.01)
    print(f"\n  {CYAN}{BOLD}EVALUATING{RESET}  {n_cand} candidates"
          f" x {n_q} queries  |  beat {best:.1%} + {thresh:.1%}")


def _print_eval_exit(d: dict, state: _CycleDisplayState) -> None:
    winner = d.get("winner_label", "?")
    w_acc = d.get("winner_accuracy", 0.0)
    improved = d.get("improved", False)
    action = d.get("next_action", "?")
    scores = d.get("candidate_scores", [])

    # Scoreboard
    board = _scoreboard(scores, winner, state.baseline_accuracy)
    if board:
        print(board)

    # Result line
    if improved:
        delta = w_acc - state.baseline_accuracy
        print(f"  {GREEN}{BOLD}✓ IMPROVED{RESET}  {w_acc:.1%}"
              f" (was {state.baseline_accuracy:.1%},"
              f" {_fmt_delta(delta)})  ->  next: {action}")
        state.baseline_accuracy = w_acc
    else:
        print(f"  {YELLOW}{BOLD}⚠ NO IMPROVEMENT{RESET}"
              f"  best candidate {w_acc:.1%}"
              f"  ->  patience {state.stall_count + 1}/{state.patience}")


def _print_refine_enter(d: dict, state: _CycleDisplayState) -> None:
    l2r = d.get("l2_round", "?")
    stalls = d.get("stall_count", "?")
    acc = d.get("current_accuracy", 0.0)
    best = d.get("best_accuracy", 0.0)
    print(f"\n  {MAGENTA}{_dotted_line('L2 REFINE CONTEXT')}{RESET}")
    print(f"  L2 round {l2r}  |  L1 stalled {stalls} rounds"
          f"  |  acc={acc:.1%}  best={best:.1%}")
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
    "growth:enter": _print_growth_enter,
    "growth:exit": _print_growth_exit,
    "analysis_eval:enter": _print_eval_enter,
    "analysis_eval:exit": _print_eval_exit,
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
    from api.services.campaign.feedback_cycle import CycleConfig, run_feedback_cycle

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
    _ds = _CycleDisplayState(baseline_accuracy=baseline_acc)
    _ds.scan_context = scan_context
    _query_counter = [0]

    def _on_phase(event: PhaseEvent) -> None:
        _dispatch_phase(event, _ds)

    def _on_query(cand_idx, n_cands, query_idx, n_queries, result):
        _query_counter[0] += 1
        is_cached = result.get("cached", False)
        prefix = (f"  [{_query_counter[0]}] C{cand_idx + 1}/{n_cands} "
                  f"Q{query_idx + 1}/{n_queries}")
        print(f"{prefix}\n{_fmt_query_result(result, cached=is_cached)}", flush=True)

    def _on_candidate(idx, total, scores):
        label = f"C{idx + 1}"
        if scores.get("cached"):
            tag = "cached"
        else:
            acc = scores["accuracy"]
            comp = scores.get("composite")
            tag = f"{acc:.1%}"
            if comp is not None and comp != acc:
                tag += f" (c={comp:.4f})"
            # Track running best
            if (_ds.best_in_round is None
                    or acc > _ds.best_in_round[1]):
                _ds.best_in_round = (label, acc)

        best_tag = ""
        if _ds.best_in_round:
            bl, ba = _ds.best_in_round
            best_tag = f"  |  running best: {bl} {ba:.1%}"
        print(f"  {CYAN}──{RESET} {label}/{total} done: {tag}{best_tag}")

    def _on_round(round_result, stall_count):
        _query_counter[0] = 0
        _ds.stall_count = stall_count
        _ds.best_in_round = None

        # Convert to campaign_rounds-compatible format and append
        ps = PromptState(**round_result.prompt_state)
        round_entry = {
            "round": len(campaign_rounds),
            "label": round_result.label,
            "prompt_state": ps,
            "accuracy": round_result.accuracy,
            "hits": round_result.hits,
            "total": round_result.total,
            "results": round_result.results,
            "candidates_evaluated": round_result.candidates_evaluated,
            "improved": round_result.improved,
        }
        campaign_rounds.append(round_entry)

        rn = _ds.round_num + 1
        print(f"\n├─ Round {rn} complete "
              f"{'─' * (70 - 20 - len(str(rn)))}┤")
        display_progress(campaign_rounds)

        if round_result.improved:
            print(f"  {GREEN}✓ Improvement detected, auto-continuing...{RESET}")
        else:
            print(f"  {YELLOW}⚠ No improvement"
                  f" ({stall_count}/{config.patience} patience){RESET}")
            if stall_count >= config.patience:
                print(f"  {RED}Stopping: patience exhausted"
                      f" ({config.patience} consecutive stalls){RESET}")

    initial_len = len(campaign_rounds)

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

    # If fully cached (no new on_round_complete callbacks fired), populate
    # campaign_rounds from the restored result so downstream cells work.
    if len(campaign_rounds) == initial_len and result.rounds:
        for rr in result.rounds:
            ps = PromptState(**rr.prompt_state)
            campaign_rounds.append({
                "round": len(campaign_rounds),
                "label": rr.label,
                "prompt_state": ps,
                "accuracy": rr.accuracy,
                "hits": rr.hits,
                "total": rr.total,
                "results": rr.results,
                "candidates_evaluated": rr.candidates_evaluated,
                "improved": rr.improved,
            })

    # Print resume info
    if result.resumed_from_round > 0:
        print(f"\n  Resumed from round {result.resumed_from_round}"
              f" ({result.resumed_from_round} rounds cached)")

    # --- Final summary ---
    if not campaign_rounds:
        print(f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
              f"stopped before any rounds completed.")
        print("  Resume: re-run this cell to restart.")
        return campaign_rounds

    best = max(campaign_rounds, key=lambda r: r["accuracy"])

    print()
    if result.stop_reason == "interrupted":
        print(_dbox_top())
        print(_dbox_line(
            f"{YELLOW}{BOLD}INTERRUPTED{RESET} — Feedback cycle stopped by user"))
        print(_dbox_sep())
        print(_dbox_line(f"Rounds       {result.n_rounds:<15d}"
                         f"Best         {best['accuracy']:.1%}"
                         f" (round {best['round']})"))
        print(_dbox_line("Stop reason  interrupted"))
        print(_dbox_line(
            "Saved: all completed rounds checkpointed to campaign store"))
        print(_dbox_line(
            "Resume: re-run this cell -- completed rounds auto-restore"))
        if result.cycle_id:
            print(_dbox_line(f"Cycle ID     {result.cycle_id}"))
        if result.langfuse_trace_id:
            print(_dbox_line(f"Langfuse     {result.langfuse_trace_id}"))
        print(_dbox_bottom())
    else:
        print(_dbox_top())
        print(_dbox_line(
            f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"))
        print(_dbox_sep())
        print(_dbox_line(f"Rounds       {result.n_rounds:<15d}"
                         f"Best         {best['accuracy']:.1%}"
                         f" (round {best['round']})"))
        print(_dbox_line(f"Stop reason  {result.stop_reason}"))
        if result.cycle_id:
            print(_dbox_line(f"Cycle ID     {result.cycle_id}"))
        if result.langfuse_trace_id:
            print(_dbox_line(f"Langfuse     {result.langfuse_trace_id}"))
        print(_dbox_bottom())

    return campaign_rounds


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
