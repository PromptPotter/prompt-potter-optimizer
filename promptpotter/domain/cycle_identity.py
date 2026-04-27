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
    from promptpotter.domain.search_point import JobSearchPoint

__all__ = ["cycle_config_identity"]


def cycle_config_identity(jsp: JobSearchPoint, dataset: list) -> str:
    """Stable identity hash for a feedback cycle's baseline ``JobSearchPoint``."""
    return f"cycle_{jsp.content_hash(dataset)[:12]}"
