"""Sensitivity scan and adaptive search wrappers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.search.adaptive_searcher import adaptive_search as _adaptive_search
from promptpotter.services.search.sensitivity_scanner import sensitivity_scan as _sensitivity_scan
from promptpotter.shared.constants import LAYER1_STRING_FIELDS

from .display import (
    CYAN,
    DIM,
    RESET,
    _fmt_query_result,
    _print_interrupt_banner,
    show_axis_profiles,
)

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.bootstrap import BackendContext

logger = logging.getLogger(__name__)

__all__ = [
    "adaptive_search",
    "run_sensitivity_scan",
    "sensitivity_scan",
]


async def sensitivity_scan(
    baseline,
    scan_variants: dict[str, list],
    dataset: list,
    backend_client=None,
    *,
    baseline_opt: OptSearchPoint | None = None,
    sample_size: int = 0,
    store=None,
    backend_id: str = "",
    pipeline_schema=None,
    experiment_id: str = "",
    session: BackendContext | None = None,
    session_id: str = "",
) -> tuple:
    """Run a sensitivity scan with progress output.

    Builds prompt_result_index internally, prints scan overview,
    runs the OAT scan, and displays profiles on completion.

    Returns (per_variant_df, axis_profiles).
    """
    # session shorthand: extract infrastructure params if provided
    if session is not None:
        backend_client = backend_client or session.backend_client
        store = store or session.store
        backend_id = backend_id or session.backend_id
        pipeline_schema = pipeline_schema or session.pipeline_schema
        index_terms = session.index_terms
        if index_terms and backend_client:
            await backend_client.init_session(index_terms)

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

    n_axes = sum(1 for v in scan_variants.values() if len(v) > 1)
    n_configs = sum(len(v) for v in scan_variants.values() if len(v) > 1)
    n_eval = sample_size if sample_size > 0 else len(dataset)
    n_cached = (
        sum(e.get("item_count", 0) for e in store.dataset_runs.list_all(backend_id))
        if store and backend_id
        else 0
    )
    print(
        f"  Axes: {n_axes}, variants: {n_configs}, "
        f"queries/variant: {n_eval}, cached results: {n_cached}"
    )
    print(f"  Estimated calls: ~{n_configs * n_eval}")

    cb, on_result_cb = _make_scan_progress_cb()

    _httpx_log = logging.getLogger("httpx")
    _httpcore_log = logging.getLogger("httpcore")
    _prev_httpx = _httpx_log.level
    _prev_httpcore = _httpcore_log.level
    _httpx_log.setLevel(logging.WARNING)
    _httpcore_log.setLevel(logging.WARNING)

    try:
        print("  Evaluating baseline...")
        df, profiles = await _sensitivity_scan(
            baseline,
            scan_variants,
            dataset,
            backend_client,
            baseline_opt=baseline_opt,
            sample_size=sample_size,
            store=store,
            backend_id=backend_id,
            pipeline_schema=pipeline_schema,
            progress_cb=cb,
            on_result=on_result_cb,
            experiment_id=experiment_id,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        _print_interrupt_banner(
            "Sensitivity scan",
            saved="completed evaluations saved via eval_search_point",
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
        store.sessions.save_scan_results(
            backend_id,
            session_id,
            df.to_dict(orient="records"),
            profiles,
        )
        logger.info("Scan results persisted to session %s", session_id)

    return df, profiles


def _make_scan_progress_cb():
    """Build a progress callback for sensitivity_scan with flip tracking.

    Returns:
        Tuple of (progress_cb, on_result_cb).
    """
    baseline_results: list = []

    def _on_result(result: dict, index: int, total: int) -> None:
        """Print each query result as it arrives from the backend."""
        is_cached = result.get("cached", False)
        print(_fmt_query_result(result, cached=is_cached), flush=True)

    def _cb(event: dict) -> None:
        t = event["type"]

        if t == "baseline_done":
            baseline_results.clear()
            baseline_results.extend(event.get("results", []))
            is_cached = event.get("cached", False)
            cached = " [cached]" if is_cached else ""
            print(f"  Baseline: {event['hits']}/{event['total']} ({event['accuracy']:.1%}){cached}")
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


async def adaptive_search(
    baseline_ps,
    variant_library: dict,
    dataset: list,
    backend_client=None,
    axis_profiles: list[dict] | None = None,
    max_rounds: int = 3,
    stop_threshold: float = 0.0,
    store=None,
    backend_id: str = "",
    pipeline_params: dict | None = None,
    index_terms: list | None = None,
    plan_id: str = "",
    experiment_id: str = "",
    session: BackendContext | None = None,
) -> tuple:
    """Run adaptive coordinate descent search with progress output.

    Returns (best_ps, best_pipeline_params, search_log_df).
    """
    # session shorthand
    if session is not None:
        backend_client = backend_client or session.backend_client
        store = store or session.store
        backend_id = backend_id or session.backend_id
        index_terms = index_terms or session.index_terms

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
            backend_client,
            axis_profiles,
            max_rounds=max_rounds,
            stop_threshold=stop_threshold,
            store=store,
            backend_id=backend_id,
            pipeline_params=pipeline_params,
            index_terms=index_terms,
            progress_cb=cb,
            plan_id=plan_id,
            experiment_id=experiment_id,
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        import pandas as _pd

        _print_interrupt_banner(
            "Adaptive search",
            saved="completed evaluations saved via eval_search_point",
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
    """Build a progress callback for adaptive_search."""

    def _cb(event: dict) -> None:
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


async def run_sensitivity_scan(
    baseline,
    campaign_config: CampaignConfig,
    scan_variants: dict[str, list],
    dataset: list,
    *,
    scan_sample_size: int = 0,
    session: BackendContext | None = None,
    experiment_id: str = "",
    session_id: str = "",
):
    """Prepare scan baseline and run sensitivity scan in one call.

    Absorbs ``decompose_scan_baseline()`` and ``sensitivity_scan()`` plumbing.

    Returns:
        (scan_baseline_sp, scan_df, axis_profiles) — the JobSearchPoint
        baseline, per-variant DataFrame, and axis profile list.
    """
    from .search import decompose_scan_baseline

    scan_baseline_sp, search_baseline, _scan_diag = await decompose_scan_baseline(
        baseline,
        campaign_config,
        session=session,
        scan_variants=scan_variants,
    )
    scan_df, axis_profiles = await sensitivity_scan(
        scan_baseline_sp,
        scan_variants,
        dataset,
        baseline_opt=search_baseline,
        sample_size=scan_sample_size,
        session=session,
        experiment_id=experiment_id,
        session_id=session_id,
    )
    return scan_baseline_sp, scan_df, axis_profiles
