"""Feedback cycle notebook wrapper: preflight, callbacks, run loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.models.pipeline_schema import PipelineSchema
    from api.services.project_store import ProjectStore

from api.models.prompt_state import PromptState

from ._display import (
    BOLD, CYAN, GREEN, RED, RESET, YELLOW,
    _box_bottom, _box_line, _box_top,
    _dbox_bottom, _dbox_line, _dbox_sep, _dbox_top,
    _fmt_delta, _fmt_query_result,
    display_progress,
)
from ._stats import (
    fmt_ci, wilson_ci,
)
from ._phase_display import (
    _CycleDisplayState, _dispatch_phase, _pp_val,
    _node_bottom, _node_line, _node_top,
)
from ._campaigns import _resolve_experiment_id

# Phase event printer for feedback cycle observability
from api.models.phase_event import PhaseEvent

__all__ = [
    "show_feedback_preflight",
    "run_feedback_cycle_notebook",
    "display_progress",
]


# ---------------------------------------------------------------------------
# Feedback cycle (preflight + run)
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
    pipeline_schema: "PipelineSchema | None" = None,
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
        pipeline_schema=pipeline_schema,
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
# Feedback cycle notebook runner
# ---------------------------------------------------------------------------


async def run_feedback_cycle_notebook(
    campaign_rounds: list,
    eval_data: list,
    campaign_config: dict,
    *,
    store: "ProjectStore | None" = None,
    backend_id: str = "",
    backend_url: str = "http://127.0.0.1:8000",
    pipeline_params: "dict | None" = None,
    pipeline_schema: "PipelineSchema | None" = None,
    session_terms: "list[str] | None" = None,
    langfuse_session_id: str | None = None,
    scan_context: "dict | None" = None,
    experiment_id: str | None = None,
    svc: dict | None = None,
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

    # svc shorthand
    if svc is not None:
        store = store or svc.get("store")
        backend_id = backend_id or svc.get("backend_id", "")
        backend_url = backend_url or svc["backend_client"].base_url
        session_terms = session_terms or svc.get("session_terms")
        pipeline_schema = pipeline_schema or svc.get("pipeline_schema")

    config = CycleConfig.from_campaign_config(
        campaign_config,
        backend_url=backend_url,
        backend_id=backend_id,
        project_root=str(store.base_dir) if store else "",
        pipeline_params=pipeline_params,
        session_terms=session_terms,
        scan_context=scan_context,
        pipeline_schema=pipeline_schema,
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
        # Reset query counter on escalation exit (on_round is skipped)
        if event.phase == "escalation" and event.event == "exit":
            _query_counter[0] = 0
            _ds.best_in_round = None
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
            base_pp = _ds.current_pipeline_params or {}
            parts = []
            for k, v in pp.items():
                old = base_pp.get(k)
                if old is not None and old != v:
                    parts.append(f"{k}: {_pp_val(old)}\u2192{_pp_val(v)}")
                else:
                    parts.append(f"{k}: {_pp_val(v)}")
            pp_line = "  ".join(parts)
            if len(pp_line) > 55:
                keys = list(pp.keys())
                pp_line = f"pp=[{', '.join(keys[:3])}]"
                if len(keys) > 3:
                    pp_line += f" +{len(keys) - 3}"
            desc = f"{desc}  {pp_line}" if desc else pp_line
        if desc:
            print(f"  {_box_line(desc, width=w)}")

        # Line 2: hits/total + composite + delta + degraded
        if scores.get("escalation_aborted"):
            eval_q = scores.get("eval_queries", n)
            expected_q = scores.get("expected_queries", n)
            stats = f"{hits}/{eval_q} hits  {YELLOW}⚠ aborted at {eval_q}/{expected_q}{RESET}"
        else:
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

        round_entry = round_result.model_dump()
        ps_raw = round_entry.get("prompt_state", {})
        round_entry["prompt_state"] = PromptState(**ps_raw) if isinstance(ps_raw, dict) else ps_raw
        round_entry["round"] = len(campaign_rounds)
        campaign_rounds.append(round_entry)

        rn = _ds.round_num + 1

        # --- ROUND SUMMARY node ---
        print()
        print(_node_top(f"ROUND {rn} SUMMARY"))

        # Inline progress table (same logic as display_progress)
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
                from api.services.campaign.critique_stats import _find_rank, _get_candidates

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

                _n_valid = sum(1 for _r in _results if not _r.get("error"))
                if _n_valid > 0:
                    for _k in [1, 5, 10]:
                        _in_top = sum(
                            1 for _r in _results if not _r.get("error")
                            and (_rank := _find_rank(_get_candidates(_r), _r.get("ground_truth", ""))) is not None
                            and _rank <= _k
                        )
                    print(_node_line(
                        f"Recall: top-1={sum(1 for _r in _results if not _r.get('error') and _find_rank(_get_candidates(_r), _r.get('ground_truth', '')) == 1) / _n_valid:.0%}"
                        f" top-5={sum(1 for _r in _results if not _r.get('error') and (_rk := _find_rank(_get_candidates(_r), _r.get('ground_truth', ''))) is not None and _rk <= 5) / _n_valid:.0%}"))
            except Exception:
                pass  # stats are best-effort

        # Improvement / patience status
        if round_result.improved:
            print(_node_line(
                f"{GREEN}✓ Improvement detected, auto-continuing...{RESET}"))
        else:
            print(_node_line(
                f"{YELLOW}⚠ No improvement"
                f" ({stall_count}/{config.patience} patience){RESET}"))
            if stall_count >= config.patience:
                print(_node_line(
                    f"{RED}Stopping: patience exhausted"
                    f" ({config.patience} consecutive stalls){RESET}"))

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
        cycle_id=resolved_cycle_id,
        experiment_id=experiment_id or "",
        backend_client=svc["backend_client"] if svc else None,
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
