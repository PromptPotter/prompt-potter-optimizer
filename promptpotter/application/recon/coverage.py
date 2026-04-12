"""Scan coverage estimation — thin wrapper over the scorer's cache-reload primitive.

For each variant a sensitivity scan is about to try, count how many diagnostic
queries are already archived in ``dataset_runs/``. Uses
``DatasetRunStore.load_reusable_results`` — the exact primitive the scorer
uses at ``application/scoring/search_point_scorer.py`` — so there is no
parallel index and no drift in cache-hit semantics between estimation and
execution.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from pydantic import BaseModel

from promptpotter.domain.opt_search_point import OptSearchPoint

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.infrastructure.store.project_store import ProjectStore


class AxisCoverage(BaseModel):
    """Per-variant coverage row."""

    kind: str  # "baseline" | "prompt_field" | "pipeline_param"
    axis: str
    value: str = ""
    n_cached: int


class CoverageEstimate(BaseModel):
    """Aggregate coverage estimate for one scan configuration."""

    n_diagnostic: int
    axes: list[AxisCoverage]
    total_cached: int
    total_needed: int


def estimate_recon_coverage(
    baseline_opt: OptSearchPoint,
    variant_library: dict,
    diagnostic_queries: list[dict],
    store: ProjectStore,
    backend_id: str,
    pipeline_schema: PipelineSchema,
    base_pipeline_params: dict | None = None,
) -> CoverageEstimate:
    """Count cached diagnostic-query hits for every variant the scan will try.

    Pure wrapper over ``DatasetRunStore.load_reusable_results`` — one call per
    variant. No new index, no global walk.
    """
    diag_qs = {q["query"] for q in diagnostic_queries if q.get("query")}
    n_diag = len(diag_qs)
    base_pp = base_pipeline_params or {}

    def _hits(chain: list[tuple[str, str]]) -> int:
        cached = store.dataset_runs.load_reusable_results(backend_id, chain)
        return sum(1 for q in diag_qs if q in cached)

    axes: list[AxisCoverage] = []

    # Baseline
    baseline_jsp = baseline_opt.to_job_search_point(
        base_pipeline_params=base_pp,
        schema=pipeline_schema,
    )
    axes.append(
        AxisCoverage(
            kind="baseline",
            axis="baseline",
            n_cached=_hits(pipeline_schema.prefix_keys(baseline_jsp.pipeline_params or {})),
        )
    )

    # Prompt-field variants
    for axis_name, values in variant_library.get("prompt_fields", {}).items():
        current = getattr(baseline_opt, axis_name, "")
        for v in values:
            if v == current:
                continue
            perturbed_jsp = baseline_opt.derive_candidate(
                **{axis_name: v},
            ).to_job_search_point(
                base_pipeline_params=base_pp,
                schema=pipeline_schema,
            )
            axes.append(
                AxisCoverage(
                    kind="prompt_field",
                    axis=axis_name,
                    value=str(v)[:80],
                    n_cached=_hits(
                        pipeline_schema.prefix_keys(perturbed_jsp.pipeline_params or {})
                    ),
                )
            )

    # Pipeline-param variants: variant_library["pipeline_params"] is flat
    # {param_name: [values]}. The node owning each param is discovered by
    # scanning the base pipeline_params dict — same approach as the old code.
    for axis_name, values in variant_library.get("pipeline_params", {}).items():
        owning_node: str | None = None
        current_val = None
        for node_name, node_cfg in base_pp.items():
            if isinstance(node_cfg, dict) and axis_name in node_cfg:
                owning_node = node_name
                current_val = node_cfg[axis_name]
                break
        if owning_node is None:
            continue
        for v in values:
            if v == current_val:
                continue
            perturbed_pp = copy.deepcopy(base_pp)
            perturbed_pp.setdefault(owning_node, {})[axis_name] = v
            perturbed_jsp = baseline_jsp.derive(pipeline_params=perturbed_pp)
            axes.append(
                AxisCoverage(
                    kind="pipeline_param",
                    axis=f"{owning_node}.{axis_name}",
                    value=str(v)[:80],
                    n_cached=_hits(
                        pipeline_schema.prefix_keys(perturbed_jsp.pipeline_params or {})
                    ),
                )
            )

    total_cached = sum(a.n_cached for a in axes)
    total_needed = sum(max(0, n_diag - a.n_cached) for a in axes)
    return CoverageEstimate(
        n_diagnostic=n_diag,
        axes=axes,
        total_cached=total_cached,
        total_needed=total_needed,
    )
