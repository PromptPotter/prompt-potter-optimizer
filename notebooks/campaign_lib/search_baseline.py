"""Scan baseline preparation."""

from __future__ import annotations

import logging

from api.models.opt_search_point import LAYER1_STRING_FIELDS
from api.services.search import (
    restructure_context_cached as _restructure_context_cached,
)

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
    model: str = "",
    temperature: float = 0.0,
):
    """Restructure baseline instruction into PromptPotter's internal fields.

    Uses alias-aware disk caching so repeated runs reuse the same
    decomposition (stable content hashes → scan cache hits).

    When *pipeline_params* is None, builds fresh defaults from
    ``configure_pipeline(svc, campaign_config)`` so the baseline content
    hash matches previous runs regardless of EXPERIMENT_ID.

    Returns:
        (baseline_jsp, search_baseline, scan_diag) — a JobSearchPoint for
        evaluation, the OptSearchPoint for display/prompt fields, and
        scan variant diagnosis dict.
    """
    import hashlib
    from .setup import configure_pipeline

    # svc shorthand: extract store/backend_id if provided
    if svc is not None:
        store = store or svc.get("store")
        backend_id = backend_id or svc.get("backend_id", "")

    # Fresh pipeline defaults when not explicitly provided
    if pipeline_params is None and svc is not None:
        pipeline_params = configure_pipeline(svc, campaign_config)

    llm_client, llm_model = setup_llm(campaign_config)

    # Resolve alias group for cache lookup
    can_cache = bool(store and backend_id)
    alias_hashes: set[str] | None = None
    if can_cache:
        original_hash = hashlib.sha256(
            baseline.render().encode(),
        ).hexdigest()[:16]
        alias_hashes = store.dataset_runs.resolve_aliases(
            backend_id, original_hash,
        )

    layer1_fields, was_cached = await _restructure_context_cached(
        baseline.instruction, llm_client,
        model=llm_model,
        store_base_dir=store.base_dir if can_cache else None,
        backend_id=backend_id,
        alias_hashes=alias_hashes,
        rp_hash=original_hash if can_cache else "",
        force=force_restructure,
    )

    search_baseline = baseline.derive_candidate(
        **{k: v for k, v in layer1_fields.items() if v},
        changes_description="search_baseline (decomposed)",
    )

    # Print decomposed fields
    cache_tag = " (cached)" if was_cached else ""
    print(f"{CYAN}Restructured baseline fields{cache_tag}:{RESET}")
    for field in LAYER1_STRING_FIELDS:
        val = getattr(search_baseline, field, "")
        if val:
            print(f"  {DIM}{field}:{RESET} {val[:80]}{'...' if len(val) > 80 else ''}")
        else:
            print(f"  {DIM}{field}:{RESET} (empty)")
    print(f"Search baseline: {search_baseline.id[:12]} "
          f"(render: {len(search_baseline.render())} chars)")

    # Historical data diagnostic via sp_hash matching
    baseline_sp = search_baseline.to_job_search_point(
        model=model,
        temperature=temperature,
        base_pipeline_params=pipeline_params,
    )

    scan_diag = None
    if can_cache:
        from api.services.search.coverage import (
            build_prompt_result_index,
            diagnose_scan_variants,
        )
        from .search_coverage import _print_historical_diagnostic

        # Register semantic equivalence (still useful for Layer 1 content-hash dedup)
        restructured_hash = hashlib.sha256(
            search_baseline.render().encode(),
        ).hexdigest()[:16]
        store.dataset_runs.register_alias(
            backend_id, original_hash, restructured_hash,
        )

        prompt_index = build_prompt_result_index(store, backend_id)

        if scan_variants:
            scan_diag = diagnose_scan_variants(
                store, backend_id, scan_variants, baseline_sp,
            )

        _print_historical_diagnostic(
            prompt_index, scan_diagnosis=scan_diag,
        )

    return baseline_sp, search_baseline, scan_diag
