"""Feedback cycle notebook wrapper: preflight, callbacks, run loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.models.phase_event import PhaseEvent
from promptpotter.services.campaign.init import (
    extract_campaign_baseline as _extract_campaign_baseline,
)
from promptpotter.services.campaign.persistence import (
    resolve_experiment_id as _resolve_experiment_id,
)
from promptpotter.services.campaign.state import RunResult
from promptpotter.services.search.cohort_analysis import (
    min_detectable_effect,
    proportion_test,
    wilson_ci,
)
from promptpotter.shared.errors import is_error_result

from .display import (
    BOLD,
    CYAN,
    GREEN,
    RED,
    RESET,
    YELLOW,
    _box_bottom,
    _box_bottom_info,
    _box_line,
    _box_top,
    _dbox_bottom,
    _dbox_line,
    _dbox_sep,
    _dbox_top,
    _fmt_delta,
    _fmt_query_result,
    _print_interrupt_banner,
    format_pipeline_overrides,
    show_progress,
)
from .phase_display import (
    _CycleDisplayState,
    _dispatch_phase,
    _node_bottom,
    _node_line,
    _node_top,
    _pp_val,
)

if TYPE_CHECKING:
    from promptpotter.models.pipeline_schema import PipelineSchema
    from promptpotter.services.campaign._campaign_utils import RunCallbacks
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.init import BackendSession
    from promptpotter.services.project_store import ProjectStore
    from promptpotter.services.search.scan_results import ScanContext

__all__ = [
    # stats
    "fmt_ci",
    "fmt_pvalue",
    # eval
    "load_baseline_prompt",
    "min_detectable_effect",
    "proportion_test",
    "run_baseline_eval",
    "run_optimization_notebook",
    "show_feedback_preflight",
    "show_progress",
    "wilson_ci",
]


# ---------------------------------------------------------------------------
# Feedback cycle (preflight + run)
# ---------------------------------------------------------------------------


def _campaign_baseline_as_dict(campaign_rounds: list) -> dict:
    """Thin wrapper: delegate to service, return as dict for local callers."""
    bl = _extract_campaign_baseline(campaign_rounds)
    return {
        "baseline_ps": bl.baseline_ps,
        "baseline_acc": bl.baseline_acc,
        "baseline_results": bl.baseline_results,
        "instruction": bl.instruction,
    }


def show_feedback_preflight(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    scan_df=None,
    axis_profiles=None,
    scan_variants=None,
    difficulty_df=None,
) -> ScanContext | None:
    """Display a rich pre-flight walkthrough for the feedback cycle.

    Builds scan context from raw DataFrames when available, then prints
    three sections: configuration summary, round pipeline walkthrough,
    and scan context preview.

    Call this in a separate notebook cell before ``run_optimization_notebook()``
    so the user can review config before committing to a run.

    Returns:
        ScanContext (or None) for passing to the run cell.
    """
    from promptpotter.services.campaign.config import RunConfig

    # Build scan context from scan data when available
    scan_context = None
    if scan_df is not None and axis_profiles is not None and scan_variants is not None:
        from promptpotter.services.search import prepare_scan_context

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

    config = RunConfig.from_campaign_config(
        campaign_config, pipeline_params=pipeline_params,
        scan_context=scan_context,
        pipeline_schema=pipeline_schema,
    )

    bl = _campaign_baseline_as_dict(campaign_rounds)

    _print_preflight_sections(
        config, bl, dataset,
        campaign_config=campaign_config,
        scan_context=scan_context,
    )

    return scan_context


def _print_preflight_sections(config, bl, dataset,
                              *, campaign_config=None, scan_context=None):
    """Print three-section preflight walkthrough."""
    baseline_acc = bl["baseline_acc"]
    instruction = bl["instruction"]
    baseline_results = bl["baseline_results"]

    _instr_preview = (instruction[:80] + "...") if len(instruction) > 80 else instruction
    _instr_preview = _instr_preview or "(empty)"
    _eff_queries = config.sample_size if config.sample_size else len(dataset)
    if config.sample_size:
        _queries_label = f"{config.sample_size} of {len(dataset)}"
    else:
        _queries_label = f"all {len(dataset)}"
    # Pipeline node display
    pp = config.pipeline_params or {}
    active_nodes = pp.get("steps", [])
    exclude = (campaign_config or {}).get("exclude_nodes", [])
    total_nodes = len(active_nodes) + len(exclude)
    if active_nodes:
        _pipeline_label = f"{len(active_nodes)} of {total_nodes} nodes"
        _nodes_detail = ", ".join(active_nodes)
    else:
        _pipeline_label = "(default pipeline)"
        _nodes_detail = None

    _est_calls = (config.max_rounds * config.n_variants * _eff_queries
                  if config.max_rounds is not None else None)

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
    print(f"  Max rounds             : {config.max_rounds or 'unlimited'}")
    print(f"  Candidates per round   : {config.n_variants}")
    print(f"  Queries per eval       : {_queries_label}")
    print(f"  Improvement threshold  : {config.improvement_threshold:.1%}")
    print(f"  Patience (L1)          : {config.l1_patience} rounds")
    print(f"  L2 (refine context)    : {_l2_label}")
    print(f"  L3 (modify plan)       : {_l3_label}")
    print("  " + "-" * 66)
    print(f"  Candidate model        : {config.model or '(default)'}")
    print(f"  Creativity             : {config.creativity}")
    print(f"  Pipeline               : {_pipeline_label}")
    if _nodes_detail:
        print(f"    Nodes                : {_nodes_detail}")
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
    print(f"     Up to {config.max_failures} failures extracted"
          f" (currently {n_failures} available)")

    # Step 3: Context assembly
    print(f"  {CYAN}3. CONTEXT ASSEMBLY{RESET}")
    print(f"     Strategy: {_strategy}")
    if scan_context:
        improving = scan_context.improving_axes
        leaderboard = scan_context.leaderboard_text
        n_leaderboard = leaderboard.count("\n") + 1 if leaderboard.strip() else 0
        tested = scan_context.tested_values
        n_tested = sum(1 for line in tested.split("\n")
                       if line.strip() and "values tested" in line) if tested else 0
        sensitivity = scan_context.sensitivity_text
        n_axes = sensitivity.count("\n") + 1 if sensitivity.strip() else 0
        difficulty = scan_context.difficulty_text

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
    print(f"     Patience: {config.l1_patience} stalls → stop  |  "
          f"L2: {_l2_label}  |  L3: {_l3_label}")

    print("  " + "-" * 66)
    if _est_calls is not None:
        print(f"  Est. backend calls     : {_est_calls}"
              f"  ({config.max_rounds}r x {config.n_variants}c"
              f" x {_eff_queries}q)")
    else:
        print(f"  Est. backend calls     : unlimited"
              f"  (no max_rounds x {config.n_variants}c"
              f" x {_eff_queries}q)")

    # ── Section 3: Scan Context Preview ──
    if scan_context:
        print()
        print(f"  {BOLD}SCAN CONTEXT PREVIEW{RESET}"
              " (injected into LLM meta-prompt)")
        print("  " + "-" * 66)

        # Leaderboard (top 10)
        leaderboard = scan_context.leaderboard_text
        if leaderboard.strip():
            lines = leaderboard.strip().split("\n")
            print(f"  {CYAN}Leaderboard:{RESET}")
            for line in lines[:10]:
                print(f"  {line}")
            if len(lines) > 10:
                print(f"    ... {len(lines) - 10} more")

        # Axis sensitivity
        sensitivity = scan_context.sensitivity_text
        if sensitivity.strip():
            print(f"  {CYAN}Axis sensitivity:{RESET}")
            for line in sensitivity.strip().split("\n"):
                print(f"  {line}")

        # Difficulty
        difficulty = scan_context.difficulty_text
        if difficulty.strip():
            print(f"  {CYAN}Query difficulty:{RESET}")
            print(f"  {difficulty.strip()}")

        # Improving axes
        improving = scan_context.improving_axes
        if improving:
            print(f"  {CYAN}Improving axes:{RESET} {', '.join(improving)}")

        # Tested values
        tested = scan_context.tested_values
        if tested.strip():
            lines = tested.strip().split("\n")
            print(f"  {CYAN}Tested values:{RESET}")
            for line in lines[:15]:
                print(f"  {line}")
            if len(lines) > 15:
                print(f"    ... {len(lines) - 15} more lines")

    print("=" * 70)


# ---------------------------------------------------------------------------
# Feedback cycle notebook runner
# ---------------------------------------------------------------------------


async def run_optimization_notebook(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    store: ProjectStore | None = None,
    backend_id: str = "",
    backend_url: str = "http://127.0.0.1:8000",
    pipeline_params: dict | None = None,
    pipeline_schema: PipelineSchema | None = None,
    index_terms: list[str] | None = None,
    langfuse_session_id: str | None = None,
    scan_context: ScanContext | None = None,
    experiment_id: str | None = None,
    session: BackendSession | None = None,
    task_context: dict | None = None,
    session_id: str = "",
    display_callbacks: RunCallbacks | None = None,
) -> tuple[list, RunResult | None]:
    """Run optimization via feedback cycle with optional L2/L3 escalation.

    Accepts and returns the ``campaign_rounds`` list format for downstream
    notebook sections.

    When ``scan_context`` is provided (from ``show_feedback_preflight()``),
    runs in scan-aware mode with per-candidate pipeline_params overrides.

    ``display_callbacks`` are entry-point-specific (display, control).
    Persistence callbacks are auto-created by the optimization loop.

    Returns:
        Tuple of (campaign_rounds, RunResult or None if interrupted).
    """
    from promptpotter.services.campaign.config import RunConfig
    from promptpotter.services.campaign.optimization_loop import run_optimization

    # session shorthand
    if session is not None:
        store = store or session.store
        backend_id = backend_id or session.backend_id
        backend_url = backend_url or session.backend_client.base_url
        index_terms = index_terms or session.index_terms
        pipeline_schema = pipeline_schema or session.pipeline_schema

    config = RunConfig.from_campaign_config(
        campaign_config,
        backend_url=backend_url,
        backend_id=backend_id,
        project_root=str(store.base_dir) if store else "",
        pipeline_params=pipeline_params,
        index_terms=index_terms,
        session_id=session_id,
        scan_context=scan_context,
        pipeline_schema=pipeline_schema,
        task_context=task_context,
    )

    bl = _campaign_baseline_as_dict(campaign_rounds)
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
        # Reset query counter on escalation exit (on_round is skipped)
        if event.phase == "escalation" and event.event == "exit":
            _query_counter[0] = 0
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

        # Line 1 (top frame): label + accuracy with CI
        acc_tag = f"{acc:.1%} {fmt_ci(ci_lo, ci_hi)}"
        print(f"  {_box_top(f'{label}/{total}', acc_tag, width=w)}")

        # Line 2 (content): cyan mutations + hits + vs baseline
        meta = {}
        if idx < len(_ds.candidates_meta):
            meta = _ds.candidates_meta[idx]
        pp = meta.get("pipeline_params_override")
        parts: list[str] = []
        if pp:
            for node, val in pp.items():
                if isinstance(val, dict):
                    for k, v in val.items():
                        parts.append(f"{node}.{k}: {_pp_val(v)}")
                else:
                    parts.append(f"{node}: {_pp_val(val)}")
        mutations = f"{CYAN}{'  '.join(parts)}{RESET}  " if parts else ""
        if scores.get("escalation_aborted"):
            eval_q = scores.get("eval_queries", n)
            expected_q = scores.get("expected_queries", n)
            hit_str = f"{hits}/{eval_q} hits {YELLOW}⚠ aborted {eval_q}/{expected_q}{RESET}"
        else:
            hit_str = f"{hits}/{n} hits"
        content = f"{mutations}{hit_str}  vs baseline: {_fmt_delta(delta)}"
        print(f"  {_box_line(content, width=w)}")

        # Line 3 (bottom frame): composite + degraded
        bottom_parts: list[str] = []
        if comp is not None and comp != acc:
            bottom_parts.append(f"composite={comp:.4f}")
        degraded = scores.get("degraded_queries", 0)
        if degraded:
            bottom_parts.append(f"{YELLOW}\u26a0 {degraded}/{n} degraded{RESET}")
        if bottom_parts:
            print(f"  {_box_bottom_info('  '.join(bottom_parts), width=w)}")
        else:
            print(f"  {_box_bottom(width=w)}")

    def _on_round(round_result, stall_count):
        _query_counter[0] = 0
        _ds.stall_count = stall_count

        round_entry = round_result.model_dump()
        ps_raw = round_entry.get("prompt_fields", {})
        round_entry["prompt_fields"] = (
            OptSearchPoint.from_prompt_fields(ps_raw) if isinstance(ps_raw, dict) else ps_raw
        )
        round_entry["round"] = len(campaign_rounds)
        campaign_rounds.append(round_entry)

        rn = _ds.round_num + 1

        # --- ROUND SUMMARY node ---
        print()
        print(_node_top(f"ROUND {rn} SUMMARY"))

        # Inline progress table (same logic as show_progress)
        _accs = []
        has_comp = any(
            rd.get("composite") is not None and rd.get("composite") != rd["accuracy"]
            for rd in campaign_rounds
        )
        if has_comp:
            print(_node_line(
                f"{'Round':<7s} {'Accuracy':>9s} {'Composite':>10s}"
                f" {'Rolling Avg':>13s} {'Trend':>8s}"))
        else:
            print(_node_line(
                f"{'Round':<7s} {'Accuracy':>9s}"
                f" {'Rolling Avg':>13s} {'Trend':>8s}"))

        for rd in campaign_rounds:
            acc = rd["accuracy"]
            _accs.append(acc)
            n_a = len(_accs)
            window_slice = _accs[-8:]
            rolling = sum(window_slice) / len(window_slice)
            if n_a <= 1:
                trend = "-"
            else:
                d = acc - _accs[-2]
                if abs(d) < 0.001:
                    trend = "+0.0%  <-- plateau"
                elif d > 0:
                    trend = f"+{d:.1%}"
                else:
                    trend = f"{d:.1%}"
            rl = str(rd["round"])
            if rd.get("round") == "grid":
                rl = "G"
            if has_comp:
                comp = rd.get("composite", acc)
                print(_node_line(
                    f"  {rl:<5s} {acc:>8.1%} {comp:>9.4f}"
                    f" {rolling:>12.1%}  {trend}"))
            else:
                print(_node_line(
                    f"  {rl:<5s} {acc:>8.1%}"
                    f" {rolling:>12.1%}  {trend}"))

        # Plateau detection
        if len(_accs) >= 3:
            recent = _accs[-3:]
            recent_avg = sum(recent) / len(recent)
            if all(abs(a - recent_avg) < 0.005 for a in recent):
                print(_node_line(
                    f"{YELLOW}-- Plateau: rolling avg stable at"
                    f" {recent_avg:.1%} for 3 rounds{RESET}"))

        print(_node_line(""))

        # Hits / evaluated
        _rr_hits = round_result.hits
        _rr_total = round_result.total
        if _rr_total == 0 and round_result.candidate_scores:
            best_cs = max(round_result.candidate_scores,
                          key=lambda s: s.get("accuracy", 0))
            _rr_hits = best_cs.get("hits", 0)
            _rr_total = best_cs.get("total", 0)
        print(_node_line(
            f"hits: {_rr_hits}/{_rr_total}"
            f"  |  evaluated: {round_result.candidates_evaluated} candidates"))

        # Per-round pipeline health stats
        if round_result.results:
            try:
                from collections import Counter

                from promptpotter.services.campaign.critique import find_rank, get_candidates
                from promptpotter.services.campaign.round_execution import (
                    _candidate_keys_from_schema,
                )
                _ck = _candidate_keys_from_schema(config.pipeline_schema)

                _results = round_result.results
                _total = len(_results)
                _td: Counter[str] = Counter()
                _deg = 0
                for _r in _results:
                    _pd = _r.get("pipeline_data") or {}
                    _td[_pd.get("terminated_at", "unknown")] += 1
                    if (_pd.get("diagnostics") or {}).get("warnings"):
                        _deg += 1

                if _td:
                    print(_node_line(
                        f"Pipeline: {' | '.join(f'{k}:{v}' for k, v in _td.most_common())}"))
                if _deg > 0:
                    print(_node_line(f"Degradation: {_deg / _total:.0%}"))

                _valid = [
                    _r for _r in _results if not is_error_result(_r)
                ]
                if _valid:
                    def _recall_at_k(k):
                        return sum(
                            1 for _r in _valid
                            if (_rk := find_rank(
                                get_candidates(_r, _ck),
                                _r.get("ground_truth", ""),
                            )) is not None and _rk <= k
                        ) / len(_valid)

                    print(_node_line(
                        f"Recall: top-1={_recall_at_k(1):.0%}"
                        f" top-5={_recall_at_k(5):.0%}"))
            except Exception:
                pass  # stats are best-effort

        # Improvement / patience status
        if round_result.improved:
            print(_node_line(
                f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
        else:
            print(_node_line(
                f"{YELLOW}⚠ No improvement"
                f" ({stall_count}/{config.l1_patience} patience){RESET}"))
            if stall_count >= config.l1_patience:
                print(_node_line(
                    f"{RED}Stopping: patience exhausted"
                    f" ({config.l1_patience} consecutive stalls){RESET}"))

        print(_node_bottom())

    # Resolve explicit experiment_id to full cycle_id
    resolved_cycle_id = None
    if experiment_id and store:
        resolved_cycle_id = _resolve_experiment_id(store, backend_id, experiment_id)
        if resolved_cycle_id is None:
            print(f"  No campaign matching '{experiment_id}' — starting fresh")
        else:
            # Load stored baseline to prevent stale notebook state from
            # overriding the original experiment's baseline accuracy.
            stored = store.campaigns.load(backend_id, resolved_cycle_id)
            if stored:
                stored_bl = stored.get("baseline_accuracy")
                if stored_bl is not None and stored_bl != baseline_acc:
                    print(f"  {YELLOW}Using stored baseline {stored_bl:.1%}"
                          f" (notebook had {baseline_acc:.1%}){RESET}")
                    baseline_acc = stored_bl

    print(f"  {YELLOW}Interrupt of cells can take up to 60 seconds!{RESET}")
    print(f"  {YELLOW}If a dialog pops up, click 'Cancel' and wait 20 seconds.{RESET}")

    from promptpotter.services.campaign._campaign_utils import RunCallbacks, chain_callbacks

    notebook_display_cb = RunCallbacks(
        on_round_complete=_on_round,
        on_candidate_eval=_on_candidate,
        on_query_eval=_on_query,
        on_phase=_on_phase,
    )
    callbacks = chain_callbacks(notebook_display_cb, display_callbacks) if display_callbacks else notebook_display_cb

    result = await run_optimization(
        instruction=instruction,
        dataset=dataset,
        config=config,
        baseline_prompt_fields=baseline_ps,
        baseline_accuracy=baseline_acc,
        baseline_results=baseline_results,
        callbacks=callbacks,
        langfuse_session_id=langfuse_session_id,
        cycle_id=resolved_cycle_id,
        experiment_id=experiment_id or "",
        backend_client=session.backend_client if session else None,
    )

    # --- Final summary ---
    if not campaign_rounds:
        print(f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
              f"stopped before any rounds completed.")
        print("  Resume: re-run this cell to restart.")
        return campaign_rounds, result

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

    format_pipeline_overrides(result.winner_pipeline_params, pipeline_schema)

    return campaign_rounds, result


# ---------------------------------------------------------------------------
# Statistical helpers (from stats.py)
# ---------------------------------------------------------------------------


def fmt_ci(lower: float, upper: float) -> str:
    """Format a CI as '[X.X%-Y.Y%]'."""
    return f"[{lower:.1%}-{upper:.1%}]"


def fmt_pvalue(p: float) -> str:
    """Format p-value with significance stars."""
    if p < 0.001:
        return "p<0.001 ***"
    if p < 0.01:
        return f"p={p:.3f} **"
    if p < 0.05:
        return f"p={p:.2f} *"
    return f"p={p:.2f} (ns)"


# ---------------------------------------------------------------------------
# Baseline eval wrapper (from eval.py)
# ---------------------------------------------------------------------------


from promptpotter.services.campaign.init import (  # noqa: E402
    load_baseline_prompt,
)
from promptpotter.services.campaign.init import (  # noqa: E402
    run_baseline_eval as _run_baseline_eval,
)


async def run_baseline_eval(
    baseline: OptSearchPoint,
    dataset: list,
    campaign_config: CampaignConfig,
    session: BackendSession,
    pipeline_params: dict | None = None,
) -> tuple:
    """Evaluate baseline prompt and initialize campaign_rounds.

    Returns:
        (campaign_rounds, baseline_results).
    """
    import asyncio

    from tqdm.auto import tqdm

    pbar = tqdm(total=len(dataset) or 1, desc="Baseline eval", unit="query")

    def _on_result(result, index, total):
        pbar.total = total
        is_cached = result.get("cached", False)
        tqdm.write(_fmt_query_result(result, cached=is_cached))
        pbar.update(1)

    from promptpotter.services.tracing.observability_logger import ObsLogger
    _obs = ObsLogger(session.store.base_dir, session.backend_id, langfuse=None)

    try:
        campaign_rounds, baseline_results = await _run_baseline_eval(
            baseline, dataset, session.backend_client,
            pipeline_params=pipeline_params,
            pipeline_schema=session.pipeline_schema,
            store=session.store, backend_id=session.backend_id,
            experiment_id=session.experiment_id,
            on_result=_on_result,
            index_terms=session.index_terms,
            obs=_obs,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        _print_interrupt_banner(
            "Baseline eval",
            completed=f"{pbar.n}/{pbar.total} queries",
            saved="completed results are cached (re-run to restart)",
            resume_hint="re-run this cell to continue from checkpoint",
        )
        return [], []
    finally:
        pbar.close()

    show_progress(campaign_rounds)

    failures = [r for r in baseline_results if not r["hit"] and not is_error_result(r)]
    for r in failures[:5]:
        print(
            f"  MISS: {r['query'][:55]}  |  "
            f"Pred: {r['predicted'][:35]}  |  GT: {r['ground_truth'][:35]}"
        )

    return campaign_rounds, baseline_results
