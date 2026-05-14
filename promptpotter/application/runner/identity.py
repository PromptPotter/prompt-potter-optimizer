"""Cycle-identity helpers — pure, no I/O.

Loop-control / strategy knobs on ``CampaignConfig`` are deliberately
excluded from the identity hash so tweaking optimizer strategy or
resuming with different budgets does not start a new cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.search_point import JobSearchPoint


def cycle_config_identity(jsp: JobSearchPoint, dataset: list) -> str:
    """Stable identity hash for a feedback cycle's origin ``JobSearchPoint``.

    Covers the rendered prompt, dataset, and full ``pipeline_params`` (active
    steps + per-node target-layer config). Loop-control / strategy knobs on
    ``CampaignConfig`` are deliberately excluded — tweaking optimizer
    strategy or resuming with different budgets does not start a new cycle.
    """
    return f"cycle_{jsp.content_hash(dataset)[:12]}"


def build_origin_cycle_id(
    osp: OptSearchPoint,
    schema: PipelineSchema | None,
    dataset: list,
) -> str:
    """Cycle ID for an origin ``OptSearchPoint`` — the OSP → JSP projection ceremony."""
    base_pp = schema.to_pipeline_params() if schema else {}
    jsp = osp.to_job_search_point(base_pipeline_params=base_pp, schema=schema)
    return cycle_config_identity(jsp, dataset)
