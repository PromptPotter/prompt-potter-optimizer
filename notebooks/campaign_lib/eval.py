"""Baseline eval wrapper with tqdm progress display."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tqdm.auto import tqdm

from promptpotter.models.opt_search_point import OptSearchPoint
from promptpotter.services.campaign.init import (
    load_baseline_prompt,
)
from promptpotter.services.campaign.init import (
    run_baseline_eval as _run_baseline_eval,
)
from promptpotter.shared.errors import is_error_result

from .display import _fmt_query_result, _print_interrupt_banner, show_progress

if TYPE_CHECKING:
    from promptpotter.services.campaign.config import CampaignConfig
    from promptpotter.services.campaign.init import BackendSession

__all__ = [
    "load_baseline_prompt", "run_baseline_eval",
]


# ---------------------------------------------------------------------------
# Baseline & Eval  (thin wrappers adding tqdm/print output)
# ---------------------------------------------------------------------------


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
    pbar = tqdm(total=len(dataset) or 1, desc="Baseline eval", unit="query")

    def _on_result(result, index, total):
        pbar.total = total
        is_cached = result.get("cached", False)
        tqdm.write(_fmt_query_result(result, cached=is_cached))
        pbar.update(1)

    from promptpotter.services.obs.observability_logger import ObsLogger
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
