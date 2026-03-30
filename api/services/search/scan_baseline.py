"""Scan baseline preparation — restructure baseline into Layer 1 fields.

Orchestrates LLM restructuring, alias registration, content hashing,
and historical diagnostic building for the sensitivity scan baseline.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from api.shared.constants import LAYER1_STRING_FIELDS

if TYPE_CHECKING:
    from api.models.opt_search_point import OptSearchPoint
    from api.models.search_point import JobSearchPoint
    from api.services.llm_client import LLMClientBase
    from api.services.project_store import ProjectStore

logger = logging.getLogger(__name__)


@dataclass
class ScanBaselineResult:
    """Result from ``prepare_scan_baseline()``."""

    baseline_jsp: JobSearchPoint
    search_baseline: OptSearchPoint
    scan_diag: dict | None
    was_cached: bool
    restructured_fields: dict[str, str]
    prompt_index: dict | None = field(default=None)


async def prepare_scan_baseline(
    baseline: OptSearchPoint,
    campaign_config: dict,
    llm_client: LLMClientBase,
    llm_model: str,
    *,
    pipeline_params: dict | None = None,
    store: ProjectStore | None = None,
    backend_id: str = "",
    scan_variants: dict | None = None,
    force_restructure: bool = False,
) -> ScanBaselineResult:
    """Restructure baseline instruction into PromptPotter's internal fields.

    Uses alias-aware disk caching so repeated runs reuse the same
    decomposition (stable content hashes -> scan cache hits).

    Returns:
        ScanBaselineResult with the JobSearchPoint baseline, OptSearchPoint
        for display/prompt fields, scan variant diagnosis, cache flag,
        and restructured field values.
    """
    from api.services.search.context import decompose_prompt_fields_cached

    # Resolve alias group for cache lookup
    can_cache = bool(store and backend_id)
    alias_hashes: set[str] | None = None
    original_hash = ""
    if can_cache:
        original_hash = hashlib.sha256(
            baseline.render().encode(),
        ).hexdigest()[:16]
        alias_hashes = store.dataset_runs.resolve_aliases(
            backend_id, original_hash,
        )

    layer1_fields, was_cached = await decompose_prompt_fields_cached(
        baseline.instruction, llm_client,
        model=llm_model,
        store_base_dir=store.base_dir if can_cache else None,
        backend_id=backend_id,
        alias_hashes=alias_hashes,
        rp_hash=original_hash if can_cache else "",
        force=force_restructure,
    )

    search_baseline = baseline.derive_candidate(
        **{k: v for k, v in layer1_fields.items() if v and k != "consultation"},
        changes_description="search_baseline (decomposed)",
    )

    # Build restructured fields dict for display
    restructured_fields = {}
    for f in LAYER1_STRING_FIELDS:
        restructured_fields[f] = getattr(search_baseline, f, "") or ""

    # Build JobSearchPoint for evaluation
    baseline_jsp = search_baseline.to_job_search_point(
        base_pipeline_params=pipeline_params,
    )

    # Historical data diagnostic via sp_hash matching
    scan_diag = None
    prompt_index = None
    if can_cache:
        from api.services.search.coverage import (
            build_prompt_result_index,
            diagnose_scan_variants,
        )

        # Register semantic equivalence
        restructured_hash = hashlib.sha256(
            search_baseline.render().encode(),
        ).hexdigest()[:16]
        store.dataset_runs.register_alias(
            backend_id, original_hash, restructured_hash,
        )

        prompt_index = build_prompt_result_index(store, backend_id)

        if scan_variants:
            scan_diag = diagnose_scan_variants(
                store, backend_id, scan_variants, baseline_jsp,
            )

    return ScanBaselineResult(
        baseline_jsp=baseline_jsp,
        search_baseline=search_baseline,
        scan_diag=scan_diag,
        was_cached=was_cached,
        restructured_fields=restructured_fields,
        prompt_index=prompt_index,
    )
