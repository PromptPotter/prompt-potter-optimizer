"""Baseline eval wrapper with tqdm progress display."""

from __future__ import annotations

import asyncio

from tqdm.auto import tqdm

from api.models.opt_search_point import OptSearchPoint
from api.services.campaign.campaign_init import (
    load_baseline_prompt, run_baseline_eval as _run_baseline_eval,
)

from .display import _fmt_query_result, _print_interrupt_banner, show_progress

__all__ = [
    "load_baseline_prompt", "run_baseline_eval",
]


# ---------------------------------------------------------------------------
# Baseline & Eval  (thin wrappers adding tqdm/print output)
# ---------------------------------------------------------------------------


async def run_baseline_eval(
    baseline: OptSearchPoint,
    eval_data: list,
    campaign_config: dict,
    svc: dict,
) -> tuple:
    """Evaluate baseline prompt and initialize campaign_rounds.

    Returns:
        (campaign_rounds, baseline_results).
    """
    pbar = tqdm(total=len(eval_data) or 1, desc="Baseline eval", unit="query")

    def _on_result(result, index, total):
        pbar.total = total
        is_cached = result.get("cached", False)
        tqdm.write(_fmt_query_result(result, cached=is_cached))
        pbar.update(1)

    from api.services.obs.observability_logger import ObsLogger
    _obs = ObsLogger(svc["store"].base_dir, svc.get("backend_id", ""), langfuse=None)

    try:
        campaign_rounds, baseline_results = await _run_baseline_eval(
            baseline, eval_data, svc.get("backend_client"),
            pipeline_params=campaign_config.get("pipeline_params"),
            store=svc.get("store"), backend_id=svc.get("backend_id", ""),
            experiment_id=svc.get("experiment_id", ""),
            on_result=_on_result,
            session_terms=svc.get("session_terms"),
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

    failures = [r for r in baseline_results if not r["hit"] and not r.get("error")]
    for r in failures[:5]:
        print(
            f"  MISS: {r['query'][:55]}  |  "
            f"Pred: {r['predicted'][:35]}  |  GT: {r['ground_truth'][:35]}"
        )

    return campaign_rounds, baseline_results
