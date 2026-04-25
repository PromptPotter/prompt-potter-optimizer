"""Feedback cycle notebook wrapper: preflight, callbacks, run loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.campaign.campaign_setup import (
    load_baseline_prompt,
)
from promptpotter.application.campaign.campaign_setup import (
    resolve_campaign_id as _resolve_campaign_id,
)
from promptpotter.application.campaign.data import (
    extract_campaign_baseline as _extract_campaign_baseline,
)
from promptpotter.application.optimization.results import RunResult
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.presentation.views import render_pipeline_overrides
from promptpotter.presentation.views.display_primitives import (
    BOLD,
    CYAN,
    GREEN,
    RESET,
    YELLOW,
    _dbox_bottom,
    _dbox_line,
    _dbox_sep,
    _dbox_top,
)
from promptpotter.presentation.views.formatting import fmt_ci, fmt_pvalue
from promptpotter.shared.statistics import (
    min_detectable_effect,
    proportion_test,
    wilson_ci,
)

from .notebook_analytics import show_progress

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import Session
    from promptpotter.application.campaign.config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema


__all__ = [
    # stats
    "fmt_ci",
    "fmt_pvalue",
    # eval
    "load_baseline_prompt",
    "min_detectable_effect",
    "proportion_test",
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
    session: Session | None = None,
    pipeline_schema: PipelineSchema | None = None,
) -> None:
    """Display a rich pre-flight walkthrough for the feedback cycle.

    Prints two sections: configuration summary and round pipeline
    walkthrough.
    """
    _bl = _extract_campaign_baseline(campaign_rounds)
    bl = {
        "baseline_ps": _bl.baseline_ps,
        "baseline_acc": _bl.baseline_acc,
        "baseline_results": _bl.baseline_results,
        "instruction": _bl.instruction,
    }

    _print_preflight_sections(
        campaign_config,
        session,
        bl,
        dataset,
        exclude_nodes=list(campaign_config.exclude_nodes),
        pipeline_schema=pipeline_schema or (session.pipeline_schema if session else None),
    )


def _print_preflight_sections(
    config: CampaignConfig,
    session: Session | None,
    bl: dict,
    dataset: list,
    *,
    exclude_nodes: list[str],
    pipeline_schema: PipelineSchema | None = None,
) -> None:
    """Print two-section preflight walkthrough."""
    baseline_acc = bl["baseline_acc"]
    instruction = bl["instruction"]
    baseline_results = bl["baseline_results"]

    from promptpotter.application.campaign.config import compute_preflight_metrics

    _instr_preview = (instruction[:80] + "...") if len(instruction) > 80 else instruction
    _instr_preview = _instr_preview or "(empty)"
    m = compute_preflight_metrics(
        config,
        session,
        len(dataset),
        exclude_nodes=exclude_nodes,
    )

    opt = config.optimization
    model_name = config.optimizer_llm.model or "(default)"

    # ── Section 1: Configuration Summary ──
    print()
    print("=" * 70)
    print(f"  {BOLD}FEEDBACK CYCLE PRE-FLIGHT{RESET}")
    print("=" * 70)
    print(f"  Baseline accuracy      : {baseline_acc:.1%}")
    print(f"  Baseline prompt        : {_instr_preview}")
    print("  " + "-" * 66)
    print(f"  Max rounds             : {opt.max_rounds or 'unlimited'}")
    print(f"  Candidates per round   : {opt.n_variants}")
    print(f"  Queries per eval       : {m.queries_label}")
    print(f"  Improvement threshold  : {opt.improvement_threshold:.1%}")
    print(f"  Patience (L1)          : {opt.l1_patience} rounds")
    print(f"  L2 (refine context)    : {m.l2_label}")
    print(f"  L3 (modify plan)       : {m.l3_label}")
    print("  " + "-" * 66)
    print(f"  Candidate model        : {model_name}")
    print(f"  Creativity             : {opt.creativity}")
    print(f"  Pipeline               : {m.pipeline_label}")
    if m.nodes_detail:
        print(f"    Nodes                : {m.nodes_detail}")
    if exclude_nodes:
        print(f"    Excluded             : {', '.join(exclude_nodes)}")

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
    print(f"     Up to {opt.max_failures} failures extracted (currently {n_failures} available)")

    # Step 3: Context assembly
    print(f"  {CYAN}3. CONTEXT ASSEMBLY{RESET}")
    print("     LLM generates from failures + L1 critique + SearchMemory")

    # Step 4: LLM candidate generation
    print(f"  {CYAN}4. LLM CANDIDATE GENERATION{RESET}")
    print(f"     Model: {model_name}  |  Temperature: {opt.creativity}")
    print(f"     Candidates: {opt.n_variants}")
    print("     Output: prompt + pipeline_params_override per candidate")

    # Step 5: Backend evaluation
    print(f"  {CYAN}5. BACKEND EVALUATION{RESET}")
    print(f"     Queries per candidate: {m.queries_label}")
    scoring = "composite (pipeline-aware)" if pipeline_schema else "accuracy"
    print(f"     Scoring: {scoring}")

    # Step 6: Winner selection
    print(f"  {CYAN}6. WINNER SELECTION{RESET}")
    print(f"     Criterion: best accuracy >= baseline + {opt.improvement_threshold:.1%}")

    # Step 7: Loop control
    print(f"  {CYAN}7. LOOP CONTROL{RESET}")
    print(
        f"     Patience: {opt.l1_patience} stalls → stop  |  L2: {m.l2_label}  |  L3: {m.l3_label}"
    )

    print("  " + "-" * 66)
    if m.est_calls is not None:
        print(
            f"  Est. backend calls     : ~{m.est_calls}"
            f"  ({opt.max_rounds}r, {opt.n_variants} candidates,"
            f" {m.eff_queries}q, sequential elimination)"
        )
    else:
        per_round = m.eff_queries + (opt.n_variants - 1) * int(m.eff_queries * 0.6)
        print(
            f"  Est. backend calls/round: ~{per_round}"
            f"  ({opt.n_variants} candidates,"
            f" {m.eff_queries}q, sequential elimination)"
        )

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
    experiment_id: str | None = None,
    session: Session | None = None,
    task_context: TaskDecomposition | dict | None = None,
    session_id: str = "",
) -> tuple[list, RunResult | None]:
    """Run optimization via feedback cycle with notebook display.

    Delegates config assembly and round-append logic to shared
    orchestration; keeps rich ANSI display callbacks.

    Returns:
        Tuple of (campaign_rounds, RunResult or None if interrupted).
    """
    from .notebook_display import NotebookDisplay

    assert session is not None, "session (Session) required for optimization"
    store = session.store
    backend_id = session.backend_id

    _bl = _extract_campaign_baseline(campaign_rounds)
    baseline_acc = _bl.baseline_acc

    from promptpotter.shared.scoring import split_scoring_block

    l1_patience = campaign_config.optimization.l1_patience
    display = NotebookDisplay(
        campaign_rounds=campaign_rounds,
        baseline_acc=baseline_acc,
        l1_patience=l1_patience,
        pipeline_schema=session.pipeline_schema,
        store=store,
        scoring_formula=split_scoring_block(campaign_config.scoring if campaign_config else None)[
            0
        ],
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

    result = await _orch_run_optimization(
        dataset,
        campaign_config,
        baseline=_bl,
        session=session,
        experiment_id=experiment_id,
        task_context=task_context,
        session_id=session_id,
        display=display,
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
        raise KeyboardInterrupt("optimization interrupted before any rounds completed")

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
    if result.session_id:
        print(_dbox_line(f"Session      {result.session_id}"))
    if result.langfuse_trace_id:
        print(_dbox_line(f"Langfuse     {result.langfuse_trace_id}"))
    print(_dbox_bottom())

    overrides_block = render_pipeline_overrides(
        result.winner_pipeline_params, session.pipeline_schema
    )
    if overrides_block:
        print()
        print(overrides_block)

    if interrupted:
        raise KeyboardInterrupt(f"optimization interrupted after {result.n_rounds} rounds")

    return campaign_rounds, result
