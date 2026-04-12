"""Feedback cycle notebook wrapper: preflight, callbacks, run loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.campaign.campaign_setup import (
    resolve_campaign_id as _resolve_campaign_id,
)
from promptpotter.application.campaign.data import (
    extract_campaign_baseline as _extract_campaign_baseline,
)
from promptpotter.application.optimization.results import RunResult
from promptpotter.application.recon.failure_groups import (
    min_detectable_effect,
    proportion_test,
    wilson_ci,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.shared.errors import is_error_result

from .display import (
    BOLD,
    CYAN,
    GREEN,
    RESET,
    YELLOW,
    _dbox_bottom,
    _dbox_line,
    _dbox_sep,
    _dbox_top,
    _fmt_query_result,
    _print_interrupt_banner,
    format_pipeline_overrides,
    show_progress,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.application.recon.recon_report import ReconBrief
    from promptpotter.domain.pipeline_schema import PipelineSchema

__all__ = [
    # stats
    "fmt_ci",
    "fmt_pvalue",
    # eval
    "load_baseline_prompt",
    "min_detectable_effect",
    "proportion_test",
    "run_baseline_scoring",
    "run_optimization_notebook",
    "show_feedback_preflight",
    "show_progress",
    "wilson_ci",
]


# ---------------------------------------------------------------------------
# Feedback cycle (preflight + run)
# ---------------------------------------------------------------------------


def show_feedback_preflight(
    campaign_rounds: list,
    dataset: list,
    campaign_config: CampaignConfig,
    *,
    pipeline_schema: PipelineSchema | None = None,
    recon_df=None,
    axis_profiles=None,
    recon_variants=None,
    difficulty_df=None,
) -> ReconBrief | None:
    """Display a rich pre-flight walkthrough for the feedback cycle.

    Builds scan context from raw DataFrames when available, then prints
    three sections: configuration summary, round pipeline walkthrough,
    and scan context preview.

    Call this in a separate notebook cell before ``run_optimization_notebook()``
    so the user can review config before committing to a run.

    Returns:
        ReconBrief (or None) for passing to the run cell.
    """
    from promptpotter.application.campaign.config import LoopConfig

    # Build scan context from scan data when available
    recon_brief = None
    if recon_df is not None and axis_profiles is not None and recon_variants is not None:
        from promptpotter.application.recon import prepare_recon_brief
        from promptpotter.application.recon.recon_report import compute_difficulty_summary

        baseline_acc = 0.0
        if campaign_rounds:
            baseline_acc = campaign_rounds[-1].get("accuracy", 0.0)

        difficulty_summary = compute_difficulty_summary(difficulty_df)

        recon_brief = prepare_recon_brief(
            recon_df,
            axis_profiles,
            recon_variants,
            baseline_acc,
            difficulty_summary=difficulty_summary,
        )

    config = LoopConfig.from_campaign_config(
        campaign_config,
        recon_brief=recon_brief,
        pipeline_schema=pipeline_schema,
    )

    _bl = _extract_campaign_baseline(campaign_rounds)
    bl = {
        "baseline_ps": _bl.baseline_ps,
        "baseline_acc": _bl.baseline_acc,
        "baseline_results": _bl.baseline_results,
        "instruction": _bl.instruction,
    }

    _print_preflight_sections(
        config,
        bl,
        dataset,
        campaign_config=campaign_config,
        recon_brief=recon_brief,
    )

    return recon_brief


def _print_preflight_sections(config, bl, dataset, *, campaign_config=None, recon_brief=None):
    """Print three-section preflight walkthrough."""
    baseline_acc = bl["baseline_acc"]
    instruction = bl["instruction"]
    baseline_results = bl["baseline_results"]

    from promptpotter.application.campaign.config import compute_preflight_metrics

    _instr_preview = (instruction[:80] + "...") if len(instruction) > 80 else instruction
    _instr_preview = _instr_preview or "(empty)"
    exclude = (campaign_config or {}).get("exclude_nodes", [])
    m = compute_preflight_metrics(
        config, len(dataset), exclude_nodes=exclude, has_recon_brief=recon_brief is not None
    )

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
    print(f"  Queries per eval       : {m.queries_label}")
    print(f"  Improvement threshold  : {config.improvement_threshold:.1%}")
    print(f"  Patience (L1)          : {config.l1_patience} rounds")
    print(f"  L2 (refine context)    : {m.l2_label}")
    print(f"  L3 (modify plan)       : {m.l3_label}")
    print("  " + "-" * 66)
    print(f"  Candidate model        : {config.model or '(default)'}")
    print(f"  Creativity             : {config.creativity}")
    print(f"  Pipeline               : {m.pipeline_label}")
    if m.nodes_detail:
        print(f"    Nodes                : {m.nodes_detail}")
    if exclude:
        print(f"    Excluded             : {', '.join(exclude)}")
    print(f"  Strategy               : {m.strategy}")

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
    from promptpotter.application.scoring.metrics import count_failures

    n_failures = count_failures(baseline_results) if baseline_results else 0
    print(f"  {CYAN}2. FAILURE ANALYSIS{RESET}")
    print(f"     Up to {config.max_failures} failures extracted (currently {n_failures} available)")

    # Step 3: Context assembly
    print(f"  {CYAN}3. CONTEXT ASSEMBLY{RESET}")
    print(f"     Strategy: {m.strategy}")
    if recon_brief:
        improving = recon_brief.improving_axes
        leaderboard = recon_brief.leaderboard_text
        n_leaderboard = leaderboard.count("\n") + 1 if leaderboard.strip() else 0
        tested = recon_brief.tested_values
        n_tested = (
            sum(1 for line in tested.split("\n") if line.strip() and "values tested" in line)
            if tested
            else 0
        )
        sensitivity = recon_brief.sensitivity_text
        n_axes = sensitivity.count("\n") + 1 if sensitivity.strip() else 0
        difficulty = recon_brief.difficulty_text

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
    print(f"     Model: {config.model or '(default)'}  |  Temperature: {config.creativity}")
    print(f"     Candidates: {config.n_variants}")
    if recon_brief:
        print("     Output: prompt + pipeline_params_override per candidate")
    else:
        print("     Output: prompt variants")

    # Step 5: Backend evaluation
    print(f"  {CYAN}5. BACKEND EVALUATION{RESET}")
    print(f"     Queries per candidate: {m.queries_label}")
    scoring = "composite (pipeline-aware)" if config.pipeline_schema else "accuracy"
    print(f"     Scoring: {scoring}")

    # Step 6: Winner selection
    print(f"  {CYAN}6. WINNER SELECTION{RESET}")
    print(f"     Criterion: best accuracy >= baseline + {config.improvement_threshold:.1%}")

    # Step 7: Loop control
    print(f"  {CYAN}7. LOOP CONTROL{RESET}")
    print(
        f"     Patience: {config.l1_patience} stalls → stop  |  L2: {m.l2_label}  |  L3: {m.l3_label}"
    )

    print("  " + "-" * 66)
    if m.est_calls is not None:
        print(
            f"  Est. backend calls     : ~{m.est_calls}"
            f"  ({config.max_rounds}r, {config.n_variants} candidates,"
            f" {m.eff_queries}q, sequential elimination)"
        )
    else:
        per_round = m.eff_queries + (config.n_variants - 1) * int(m.eff_queries * 0.6)
        print(
            f"  Est. backend calls/round: ~{per_round}"
            f"  ({config.n_variants} candidates,"
            f" {m.eff_queries}q, sequential elimination)"
        )

    # ── Section 3: Scan Context Preview ──
    if recon_brief:
        print()
        print(f"  {BOLD}SCAN CONTEXT PREVIEW{RESET} (injected into LLM meta-prompt)")
        print("  " + "-" * 66)

        # Leaderboard (top 10)
        leaderboard = recon_brief.leaderboard_text
        if leaderboard.strip():
            lines = leaderboard.strip().split("\n")
            print(f"  {CYAN}Leaderboard:{RESET}")
            for line in lines[:10]:
                print(f"  {line}")
            if len(lines) > 10:
                print(f"    ... {len(lines) - 10} more")

        # Axis sensitivity
        sensitivity = recon_brief.sensitivity_text
        if sensitivity.strip():
            print(f"  {CYAN}Axis sensitivity:{RESET}")
            for line in sensitivity.strip().split("\n"):
                print(f"  {line}")

        # Difficulty
        difficulty = recon_brief.difficulty_text
        if difficulty.strip():
            print(f"  {CYAN}Query difficulty:{RESET}")
            print(f"  {difficulty.strip()}")

        # Improving axes
        improving = recon_brief.improving_axes
        if improving:
            print(f"  {CYAN}Improving axes:{RESET} {', '.join(improving)}")

        # Tested values
        tested = recon_brief.tested_values
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
    langfuse_session_id: str | None = None,
    recon_brief: ReconBrief | None = None,
    experiment_id: str | None = None,
    session: SessionEnv | None = None,
    task_context: TaskDecomposition | dict | None = None,
    session_id: str = "",
) -> tuple[list, RunResult | None]:
    """Run optimization via feedback cycle with notebook display.

    Delegates config assembly and round-append logic to shared
    orchestration; keeps rich ANSI display callbacks.

    Returns:
        Tuple of (campaign_rounds, RunResult or None if interrupted).
    """
    from .display_callbacks import NotebookDisplay

    assert session is not None, "session (SessionEnv) required for optimization"
    store = session.store
    backend_id = session.backend_id

    _bl = _extract_campaign_baseline(campaign_rounds)
    baseline_acc = _bl.baseline_acc

    # --- Warn if scan context was lost (kernel restart) ---
    if recon_brief is None and any(r.get("round") == "search" for r in campaign_rounds):
        print(f"  {YELLOW}⚠ Scan context not available — running without scan data.{RESET}")
        print("    Run the preflight cell to rebuild recon_brief from scan variables.")

    l1_patience = campaign_config.get("optimization", {}).get("l1_patience", 3)
    display = NotebookDisplay(
        campaign_rounds=campaign_rounds,
        baseline_acc=baseline_acc,
        l1_patience=l1_patience,
        pipeline_schema=session.pipeline_schema,
        recon_brief=recon_brief,
    )

    # Resolve explicit experiment_id to full cycle_id
    resolved_cycle_id = None
    if experiment_id and store:
        resolved_cycle_id = _resolve_campaign_id(store, backend_id, experiment_id)
        if resolved_cycle_id is None:
            print(f"  No campaign matching '{experiment_id}' — starting fresh")
        else:
            # Load stored baseline to prevent stale notebook state from
            # overriding the original experiment's baseline accuracy.
            stored = store.campaigns.load(backend_id, resolved_cycle_id)
            if stored:
                stored_bl = stored.get("baseline_accuracy")
                if stored_bl is not None and stored_bl != baseline_acc:
                    print(
                        f"  {YELLOW}Using stored baseline {stored_bl:.1%}"
                        f" (notebook had {baseline_acc:.1%}){RESET}"
                    )
                    baseline_acc = stored_bl

    print(f"  {YELLOW}Interrupt of cells can take up to 60 seconds!{RESET}")
    print(f"  {YELLOW}If a dialog pops up, click 'Cancel' and wait 20 seconds.{RESET}")

    from promptpotter.application.campaign.runner import (
        run_optimization as _orch_run_optimization,
    )

    callbacks = display.as_callbacks()

    result = await _orch_run_optimization(
        dataset,
        campaign_config,
        baseline=_bl,
        session=session,
        recon_brief=recon_brief,
        experiment_id=experiment_id,
        task_context=task_context,
        session_id=session_id,
        callbacks=callbacks,
        langfuse_session_id=langfuse_session_id,
        cycle_id=resolved_cycle_id,
    )

    # --- Final summary ---
    if not campaign_rounds:
        print(
            f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
            f"stopped before any rounds completed."
        )
        print("  Resume: re-run this cell to restart.")
        return campaign_rounds, result

    best = max(campaign_rounds, key=lambda r: r["accuracy"])
    if result is None:
        return campaign_rounds, result
    interrupted = result.stop_reason == "interrupted"
    title = (
        f"{YELLOW}{BOLD}INTERRUPTED{RESET} — stopped by user"
        if interrupted
        else f"{GREEN}{BOLD}OPTIMIZATION COMPLETE{RESET}"
    )

    print()
    print(_dbox_top())
    print(_dbox_line(title))
    print(_dbox_sep())
    print(
        _dbox_line(
            f"Rounds       {result.n_rounds:<15d}"
            f"Best         {best['accuracy']:.1%}"
            f" (round {best['round']})"
        )
    )
    print(_dbox_line(f"Stop reason  {result.stop_reason}"))
    if interrupted:
        print(_dbox_line("Resume: re-run this cell -- rounds auto-restore"))
    if result.cycle_id:
        print(_dbox_line(f"Cycle ID     {result.cycle_id}"))
    if result.langfuse_trace_id:
        print(_dbox_line(f"Langfuse     {result.langfuse_trace_id}"))
    print(_dbox_bottom())

    format_pipeline_overrides(result.winner_pipeline_params, session.pipeline_schema)

    return campaign_rounds, result


# ---------------------------------------------------------------------------
# Statistical helpers — imported from display.py
# ---------------------------------------------------------------------------
# Baseline eval wrapper (from eval.py)
# ---------------------------------------------------------------------------
from promptpotter.application.campaign.campaign_setup import (  # noqa: E402
    load_baseline_prompt,
)
from promptpotter.application.campaign.data import (  # noqa: E402
    run_baseline_scoring as _run_baseline_scoring,
)

from .display import fmt_ci, fmt_pvalue  # noqa: E402


async def run_baseline_scoring(
    baseline: OptSearchPoint,
    dataset: list,
    campaign_config: CampaignConfig,
    session: SessionEnv,
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

    from promptpotter.infrastructure.tracing.observability_logger import ObsLogger

    _obs = ObsLogger(session.store.base_dir, session.backend_id, langfuse=None)

    try:
        campaign_rounds, baseline_results = await _run_baseline_scoring(
            baseline,
            dataset,
            session,
            pipeline_params=pipeline_params,
            pipeline_schema=session.pipeline_schema,
            experiment_id=session.experiment_id,
            on_result=_on_result,
            obs=_obs,
            scoring_formula=campaign_config.get("scoring"),
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
