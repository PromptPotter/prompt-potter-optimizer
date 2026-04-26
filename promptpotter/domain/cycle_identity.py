"""Cycle-identity hashing — decides whether two runs share a feedback cycle.

The hash covers only *what problem* the loop is solving (active pipeline
steps + baseline prompt + dataset). Loop-control / strategy knobs
(``max_rounds``, ``model``, patience, n_variants, …) are deliberately
excluded so tweaking optimizer strategy or resuming with different
budgets does not create a new cycle and discard cached candidates.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig

__all__ = ["cycle_config_identity", "cycle_hash_suffix"]


def _qg_pair(d: Any) -> tuple[str, str]:
    """Extract (query, ground_truth) from a Sample or legacy dict."""
    if hasattr(d, "query"):
        return d.query, d.ground_truth
    return d.get("query", ""), d.get("ground_truth", "")


def cycle_config_identity(
    config: CampaignConfig,
    baseline_rendered: str,
    dataset: list,
    active_steps: list[str],
) -> str:
    """Stable identity hash for a feedback cycle configuration.

    Hashes only the *problem* (active pipeline steps + baseline prompt +
    dataset). Loop-control / strategy knobs are excluded so tweaking
    optimizer strategy, resuming, or changing model between runs does not
    create a new cycle.

    ``active_steps`` is passed explicitly — keeps ``domain/`` free of
    imports from ``application/`` (``Session`` owns the pipeline schema).
    """
    payload_dict: dict[str, Any] = {
        "active_steps": list(active_steps),
        "baseline_rendered": baseline_rendered,
        "dataset_pairs": sorted(_qg_pair(d) for d in dataset),
    }
    payload = json.dumps(payload_dict, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


def cycle_hash_suffix(
    config: CampaignConfig,
    baseline_rendered: str,
    dataset: list,
    active_steps: list[str],
) -> str:
    """Return the 12-hex content hash without the ``cycle_`` prefix."""
    return cycle_config_identity(config, baseline_rendered, dataset, active_steps).removeprefix(
        "cycle_"
    )
