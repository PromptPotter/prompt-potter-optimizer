"""Sensitivity scan and adaptive search wrappers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from promptpotter.application.recon.adaptive_recon import ReconEvent
from promptpotter.application.recon.adaptive_recon import run_adaptive_recon as _adaptive_search
from promptpotter.application.recon.recon_runner import (
    run_recon as _run_recon,
)
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.shared.constants import LAYER1_STRING_FIELDS

from .notebook_phase import show_axis_profiles
from .notebook_primitives import (
    CYAN,
    DIM,
    RESET,
    _fmt_query_result,
    _print_interrupt_banner,
)

if TYPE_CHECKING:
    from promptpotter.application.campaign.campaign_setup import SessionEnv
    from promptpotter.application.campaign.config import CampaignConfig

logger = logging.getLogger(__name__)

__all__ = [
    "run_adaptive_recon",
    "run_recon",
    "run_sensitivity_recon",
]


async def run_recon(
    baseline,
    recon_variants: dict[str, list | dict],
    dataset: list,
    session: SessionEnv,
    *,
    baseline_opt: OptSearchPoint | None = None,
    sample_size: int = 0,
    pipeline_schema=None,
    experiment_id: str = "",
    session_id: str = "",
    scoring_formula: str | None = None,
) -> tuple:
    """Run a sensitivity scan with progress output.

    Builds prompt_result_index internally, prints scan overview,
    runs the OAT scan, and displays profiles on completion.

    Returns (per_variant_df, axis_profiles).
    """
    store = session.store
    backend_id = session.backend_id
    pipeline_schema = pipeline_schema or session.pipeline_schema
    if session.index_terms:
        await session.backend_client.init_session(session.index_terms)

    # Print scan overview
    print("Running sensitivity scan...")
    if baseline_opt is not None:
        print(f"{CYAN}Baseline field values:{RESET}")
        for field in LAYER1_STRING_FIELDS:
            val = getattr(baseline_opt, field, "")
            if val:
                print(f"  {DIM}{field}:{RESET} {val[:80]}{'...' if len(val) > 80 else ''}")
            else:
                print(f"  {DIM}{field}:{RESET} (empty)")
        print()

    n_axes = sum(1 for v in recon_variants.values() if len(v) > 1)
    n_configs = sum(len(v) for v in recon_variants.values() if len(v) > 1)
    n_samples = sample_size if sample_size > 0 else len(dataset)
    n_cached = (
        sum(e.get("item_count", 0) for e in store.dataset_runs.list_all(backend_id))
        if store and backend_id
        else 0
    )
    print(
        f"  Axes: {n_axes}, variants: {n_configs}, "
        f"queries/variant: {n_samples}, cached results: {n_cached}"
    )
    print(f"  Estimated calls: ~{n_configs * n_samples}")

    cb, on_result_cb = _make_scan_progress_cb()

    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)

    try:
        print("  Evaluating baseline...")
        df, profiles = await _run_recon(
            baseline,
            recon_variants,
            dataset,
            session,
            baseline_opt=baseline_opt,
            sample_size=sample_size,
            pipeline_schema=pipeline_schema,
            progress_cb=cb,
            on_result=on_result_cb,
            experiment_id=experiment_id,
            scoring_formula=scoring_formula,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        _print_interrupt_banner(
            "Sensitivity scan",
            saved="completed evaluations saved via score_search_point",
            resume_hint="re-run this cell -- cached evals will be reused",
        )
        return None, []
    finally:
        _httpx_log.setLevel(_prev_httpx)
        _httpcore_log.setLevel(_prev_httpcore)

    if df is None or (hasattr(df, "empty") and df.empty):
        print("\n  [ABORT] Scan returned no results -- backend may be down.")
        print("  Check backend status and model configuration, then re-run.")
        return None, []

    print(f"\nSensitivity scan complete: {len(df)} variants evaluated")
    show_axis_profiles(profiles)

    # Persist scan results via SessionStore if session is active
    if session_id and store and backend_id:
        store.sessions.save_recon_results(
            backend_id,
            session_id,
            df.to_dict(orient="records"),
            profiles,
        )
        logger.info("Scan results persisted to session %s", session_id)

    return df, profiles


def _make_scan_progress_cb() -> tuple[
    Callable[[ReconEvent], None], Callable[[dict, int, int], None]
]:
    """Build a progress callback for run_recon with flip tracking."""
    baseline_results: list[dict] = []

    def _on_result(result: dict, index: int, total: int) -> None:
        """Print each query result as it arrives from the backend."""
        is_cached = result.get("cached", False)
        print(_fmt_query_result(result, cached=is_cached), flush=True)

    def _cb(event: ReconEvent) -> None:
        t = event["type"]

        if t == "baseline_done":
            baseline_results.clear()
            baseline_results.extend(event.get("results", []))
            bl_cached = event.get("cached", False)
            cached_tag = " [cached]" if bl_cached else ""
            print(
                f"  Baseline: {event['hits']}/{event['total']} ({event['accuracy']:.1%}){cached_tag}"
            )
            # Per-query lines already printed by _on_result in real-time

        elif t == "axis_start":
            ai = event["axis_index"] + 1
            total = event["total_axes"]
            card = event["cardinality"]
            print(f"\n{'=' * 70}")
            print(f"  Axis {ai}/{total}: {event['axis']} ({event['axis_type']}, {card} values)")
            print(f"{'=' * 70}")

        elif t == "variant_done":
            vi = event["value_idx"]
            preview = event["value_preview"]
            hits = event["hits"]
            total = event["total"]
            acc = event["accuracy"]
            delta = event["delta"]
            is_bl = event["is_baseline_value"]
            cached = event.get("cached", False)

            if is_bl:
                delta_str = "(baseline)"
                marker = ""
            elif delta > 0:
                delta_str = f"+{delta:.1%}"
                marker = " ^"
            elif delta < 0:
                delta_str = f"{delta:.1%}"
                marker = " v"
            else:
                delta_str = "+0.0%"
                marker = ""

            cache_str = " [cached]" if cached else ""
            print(
                f"  [{vi}] {preview:<42s} {hits}/{total}  {acc:.1%}  {delta_str}{marker}{cache_str}"
            )
            # Per-query lines already printed by _on_result in real-time

        elif t == "axis_done":
            budget = event["exploration_budget"]
            sr = event["sensitivity_range"]
            bd = event["best_delta"]
            wd = event["worst_delta"]
            print(
                f"  >> {event['axis']}: range={sr:.1%}, "
                f"best={bd:+.1%}, worst={wd:+.1%}, budget={budget}"
            )

        elif t == "axis_pruned":
            tested = event["variants_tested"]
            remaining = event["remaining_skipped"]
            print(
                f"  >> PRUNED: {event['axis']} — {tested} variants "
                f"overlap baseline CI, skipping {remaining} remaining"
            )

        elif t == "scan_aborted":
            reason = event.get("reason", "unknown")
            print(f"\n{'!' * 70}")
            print(f"  SCAN ABORTED: {reason}")
            print(f"{'!' * 70}")

    return _cb, _on_result


async def run_adaptive_recon(
    baseline_ps,
    variant_library: dict,
    dataset: list,
    session: SessionEnv,
    axis_profiles: list[dict] | None = None,
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    pipeline_params: dict | None = None,
    plan_id: str = "",
    experiment_id: str = "",
) -> tuple:
    """Run adaptive coordinate descent search with progress output.

    Returns (best_ps, best_pipeline_params, search_log_df).
    """

    active = [p for p in (axis_profiles or []) if p["exploration_budget"] != "skip"]
    print(f"Adaptive search: {len(active)} active axes, max {max_rounds} rounds")
    for p in active:
        print(
            f"  {p['axis']} ({p['axis_type']}): "
            f"card={p['cardinality']}, budget={p['exploration_budget']}"
        )

    cb = _make_search_progress_cb()

    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)
    try:
        best_ps, best_params, log_df = await _adaptive_search(
            baseline_ps,
            variant_library,
            dataset,
            session,
            axis_profiles or [],
            max_rounds=max_rounds,
            stop_threshold=stop_threshold,
            pipeline_params=pipeline_params,
            progress_cb=cb,
            plan_id=plan_id,
            experiment_id=experiment_id,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        import pandas as _pd

        _print_interrupt_banner(
            "Adaptive search",
            saved="completed evaluations saved via score_search_point",
            resume_hint="re-run this cell to restart (cached evals will be reused)",
        )
        return baseline_ps, dict(pipeline_params or {}), _pd.DataFrame()
    finally:
        _httpx_log.setLevel(_prev_httpx)
        _httpcore_log.setLevel(_prev_httpcore)

    if not log_df.empty:
        print(f"\nSearch log: {len(log_df)} evaluations across {log_df['round'].nunique()} rounds")
        best_row = log_df.loc[log_df["accuracy"].idxmax()]
        print(
            f"  Best found: {best_row['axis']}={best_row['value_preview']} "
            f"({best_row['accuracy']:.1%})"
        )
    else:
        print("\nNo evaluations performed (all axes skipped).")

    return best_ps, best_params, log_df


def _make_search_progress_cb():
    """Build a progress callback for run_adaptive_recon."""

    def _cb(event: ReconEvent) -> None:
        t = event["type"]

        if t == "round_start":
            r = event["round"]
            max_r = event["max_rounds"]
            acc = event["current_accuracy"]
            axes = event["active_axes"]
            print(f"\n{'=' * 70}")
            print(f"  Round {r}/{max_r} | current accuracy: {acc:.1%}")
            print(f"  Active axes: {', '.join(axes)}")
            print(f"{'=' * 70}")

        elif t == "axis_start":
            axis = event["axis"]
            card = event["cardinality"]
            budget = event["budget"]
            print(f"\n  -- {axis} ({event['axis_type']}, {card} values, budget={budget}) --")

        elif t == "variant_done":
            preview = event["value_preview"]
            hits = event["hits"]
            total = event["total"]
            acc = event["accuracy"]
            delta = event["delta"]
            cached = event.get("cached", False)

            if delta > 0:
                delta_str = f"+{delta:.1%}"
                marker = " ^"
            elif delta < 0:
                delta_str = f"{delta:.1%}"
                marker = " v"
            else:
                delta_str = "+0.0%"
                marker = ""

            cache_str = " [cached]" if cached else ""
            print(f"    {preview:<42s} {hits}/{total}  {acc:.1%}  {delta_str}{marker}{cache_str}")

            results = event.get("results", [])
            for r in results:
                print(_fmt_query_result(r, cached=cached))

        elif t == "axis_resolved":
            action = event["action"]
            axis = event["axis"]
            if action == "improved":
                imp = event["improvement"]
                bv = event["best_value"]
                new_acc = event["new_accuracy"]
                print(f"  ** {axis} IMPROVED +{imp:.1%} -> {new_acc:.1%} (best: {bv})")
            else:
                print(f"  -- {axis}: no improvement, resolved")

        elif t == "round_done":
            r = event["round"]
            acc = event["accuracy"]
            if event["improved"]:
                print(f"\n  Round {r} done: accuracy now {acc:.1%}")
            else:
                print(f"\n  Round {r}: no improvement, stopping.")

    return _cb


async def run_sensitivity_recon(
    baseline,
    campaign_config: CampaignConfig,
    recon_variants: dict[str, list | dict],
    dataset: list,
    *,
    recon_sample_size: int = 0,
    session: SessionEnv | None = None,
    experiment_id: str = "",
    session_id: str = "",
):
    """Prepare scan baseline and run sensitivity scan in one call.

    Absorbs ``decompose_recon_baseline()`` and ``run_recon()`` plumbing.

    Returns:
        (recon_baseline_sp, recon_df, axis_profiles) — the JobSearchPoint
        baseline, per-variant DataFrame, and axis profile list.
    """
    from .search import decompose_recon_baseline

    recon_baseline_sp, search_baseline = await decompose_recon_baseline(
        baseline,
        campaign_config,
        session=session,
        recon_variants=recon_variants,
    )
    assert session is not None
    recon_df, axis_profiles = await run_recon(
        recon_baseline_sp,
        recon_variants,
        dataset,
        baseline_opt=search_baseline,
        sample_size=recon_sample_size,
        session=session,
        experiment_id=experiment_id,
        session_id=session_id,
        scoring_formula=campaign_config.get("scoring"),
    )
    return recon_baseline_sp, recon_df, axis_profiles
