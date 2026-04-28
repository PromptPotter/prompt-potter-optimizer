"""Cycle-identity hashing — decides whether two runs share a feedback cycle.

The hash is the baseline ``JobSearchPoint``'s content hash over the
dataset. ``JobSearchPoint.content_hash`` covers the rendered prompt,
the dataset, and the full ``pipeline_params`` dict (active steps +
per-node target-layer config: model, temperature, max_tokens, …).
Loop-control / strategy knobs on ``CampaignConfig`` (``max_rounds``,
optimizer-LLM model, patience, n_variants, …) are deliberately not
part of a ``JobSearchPoint`` and so are excluded — tweaking optimizer
strategy or resuming with different budgets does not start a new
cycle and discard cached candidates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint

__all__ = ["build_baseline_cycle_id", "cycle_config_identity"]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list) -> str:
    """Stable identity hash for a feedback cycle's baseline ``JobSearchPoint``."""
    return f"cycle_{jsp.content_hash(dataset)[:12]}"


def build_baseline_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list,
) -> str:
    """Cycle ID for a baseline ``OptSearchPoint`` — the ``osp → JSP → cycle_config_identity`` ceremony."""
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)
