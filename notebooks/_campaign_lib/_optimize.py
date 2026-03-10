"""Optimization rounds, feedback cycle, and Langfuse integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from api.models.prompt_state import PromptState
from api.services.project_store import ProjectStore

from ._display import (
    BOLD, RESET, YELLOW, _fmt_query_result, display_progress,
)

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
) -> None:
    """Display a pre-flight summary box for the feedback cycle.

    Call this in a separate notebook cell before ``run_feedback_cycle_notebook()``
    so the user can review config before committing to a run.
    """
    from api.services.campaign.feedback_cycle import CycleConfig

    config = CycleConfig.from_campaign_config(
        campaign_config, pipeline_params=pipeline_params,
    )

    bl = _extract_campaign_baseline(campaign_rounds)

    _print_preflight_box(
        config, bl["baseline_acc"], bl["instruction"], eval_data, pipeline_params,
    )


def _print_preflight_box(config, baseline_acc, instruction, eval_data, pipeline_params):
    """Print the pre-flight summary box (shared by preflight + cycle)."""
    _instr_preview = (instruction[:80] + "...") if len(instruction) > 80 else instruction
    _instr_preview = _instr_preview or "(empty)"
    _eff_queries = config.queries_per_eval if config.queries_per_eval else len(eval_data)
    if config.queries_per_eval:
        _queries_label = f"{config.queries_per_eval} of {len(eval_data)}"
    else:
        _queries_label = f"all {len(eval_data)}"
    _steps = pipeline_params.get("steps", []) if pipeline_params else []
    _steps_label = ", ".join(_steps) if _steps else "(default pipeline)"
    _est_calls = config.max_rounds * config.n_variants * _eff_queries

    _l2_label = (f"enabled, patience={config.l2_patience}"
                 if config.enable_l2 else "disabled")
    _l3_label = (f"enabled, patience={config.l3_patience}"
                 if config.enable_l3 else "disabled")

    print()
    print("=" * 70)
    print("  FEEDBACK CYCLE - PRE-FLIGHT")
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
    print(f"  Active steps           : {_steps_label}")
    print("  " + "-" * 66)
    print(f"  Est. backend calls     : {_est_calls}"
          f"  ({config.max_rounds} rounds x {config.n_variants} cands"
          f" x {_eff_queries} queries)")
    print("=" * 70)


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
) -> list:
    """Run optimization via feedback cycle with optional L2/L3 escalation.

    Accepts and returns the ``campaign_rounds`` list format for downstream
    notebook sections.

    Internally uses InitNode -> GrowFilterNode -> AnalysisEvalNode with Langfuse
    tracing and ``next_action`` routing.

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
    )

    bl = _extract_campaign_baseline(campaign_rounds)
    baseline_ps = bl["baseline_ps"]
    baseline_acc = bl["baseline_acc"]
    baseline_results = bl["baseline_results"]
    instruction = bl["instruction"]

    # --- Pre-flight summary ---
    _print_preflight_box(config, baseline_acc, instruction, eval_data, pipeline_params)

    _query_counter = [0]  # mutable counter for closure

    def _on_query(cand_idx, n_cands, query_idx, n_queries, result):
        _query_counter[0] += 1
        is_cached = result.get("cached", False)
        prefix = (f"  [{_query_counter[0]}] C{cand_idx + 1}/{n_cands} "
                  f"Q{query_idx + 1}/{n_queries}")
        print(f"{prefix}\n{_fmt_query_result(result, cached=is_cached)}", flush=True)

    def _on_candidate(idx, total, scores):
        if scores.get("cached"):
            tag = "cached"
        else:
            comp = scores.get("composite")
            tag = f"{scores['accuracy']:.1%}"
            if comp is not None and comp != scores['accuracy']:
                tag += f" (c={comp:.4f})"
        print(f"  >> Candidate {idx + 1}/{total} done: {tag}")

    def _on_round(round_result, stall_count):
        _query_counter[0] = 0
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
        display_progress(campaign_rounds)

        if round_result.improved:
            print("\nImprovement detected, auto-continuing...")
        else:
            print(f"\nNo improvement ({stall_count}/{config.patience} patience)")
            if stall_count >= config.patience:
                print(
                    f"\nStopping: no improvement for {config.patience}"
                    " consecutive rounds."
                )

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
        print(
            f"\nResumed from round {result.resumed_from_round} "
            f"({result.resumed_from_round} rounds cached)"
        )

    # Final summary
    if not campaign_rounds:
        print(f"\n{YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle "
              f"stopped before any rounds completed.")
        print("  Resume: re-run this cell to restart.")
        return campaign_rounds

    best = max(campaign_rounds, key=lambda r: r["accuracy"])
    print(f"\n{'=' * 70}")
    if result.stop_reason == "interrupted":
        print(f"  {YELLOW}{BOLD}[INTERRUPTED]{RESET} Feedback cycle stopped by user")
    else:
        print("OPTIMIZATION COMPLETE (feedback cycle)")
    print(f"  Rounds run: {result.n_rounds}")
    print(f"  Best accuracy: {best['accuracy']:.1%} (round {best['round']})")
    print(f"  Stop reason: {result.stop_reason}")
    if result.stop_reason == "interrupted":
        print("  Saved: all completed rounds checkpointed to campaign store")
        print("  Resume: re-run this cell -- completed rounds auto-restore")
    if result.cycle_id:
        print(f"  Cycle ID: {result.cycle_id}")
    if result.langfuse_trace_id:
        print(f"  Langfuse trace: {result.langfuse_trace_id}")
    print(f"{'=' * 70}")

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
