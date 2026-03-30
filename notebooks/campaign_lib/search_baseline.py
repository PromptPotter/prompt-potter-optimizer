"""Scan baseline preparation — thin display wrapper."""

from __future__ import annotations

import logging

from api.services.search.scan_baseline import (
    prepare_scan_baseline as _prepare_scan_baseline,
)
from api.shared.constants import LAYER1_STRING_FIELDS

from .display import CYAN, DIM, RESET
from .setup import setup_llm

logger = logging.getLogger(__name__)

__all__ = [
    "prepare_scan_baseline",
]


async def prepare_scan_baseline(
    baseline,
    campaign_config: dict,
    pipeline_params: dict | None = None,
    *,
    store=None,
    backend_id: str = "",
    scan_variants: dict | None = None,
    force_restructure: bool = False,
    svc: dict | None = None,
):
    """Restructure baseline instruction into PromptPotter's internal fields.

    Delegates to ``api.services.search.scan_baseline.prepare_scan_baseline()``
    and prints restructured fields + historical diagnostic.

    Returns:
        (baseline_jsp, search_baseline, scan_diag)
    """
    from .setup import configure_pipeline

    # svc shorthand: extract store/backend_id if provided
    if svc is not None:
        store = store or svc.get("store")
        backend_id = backend_id or svc.get("backend_id", "")

    # Fresh pipeline defaults when not explicitly provided
    if pipeline_params is None and svc is not None:
        pipeline_params = configure_pipeline(svc, campaign_config)

    llm_client, llm_model = setup_llm(campaign_config)

    result = await _prepare_scan_baseline(
        baseline, campaign_config, llm_client, llm_model,
        pipeline_params=pipeline_params,
        store=store,
        backend_id=backend_id,
        scan_variants=scan_variants,
        force_restructure=force_restructure,
    )

    # Print decomposed fields
    cache_tag = " (cached)" if result.was_cached else ""
    print(f"{CYAN}Restructured baseline fields{cache_tag}:{RESET}")
    for field in LAYER1_STRING_FIELDS:
        val = result.restructured_fields.get(field, "")
        if val:
            print(f"  {DIM}{field}:{RESET} {val[:80]}{'...' if len(val) > 80 else ''}")
        else:
            print(f"  {DIM}{field}:{RESET} (empty)")
    print(f"Search baseline: {result.search_baseline.id[:12]} "
          f"(render: {len(result.search_baseline.render())} chars)")

    # Historical diagnostic display
    if result.prompt_index is not None:
        from .search_coverage import _print_historical_diagnostic

        _print_historical_diagnostic(
            result.prompt_index, scan_diagnosis=result.scan_diag,
        )

    return result.baseline_jsp, result.search_baseline, result.scan_diag
