"""Cycle-identity hashing — decides whether two runs share a feedback cycle.

See ``docs/architecture/optimization.md § Cycle Identity`` for the full
two-tier (experiment vs strict) specification.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import CampaignConfig

__all__ = ["TUNING_KEYS", "cycle_config_identity", "cycle_hash_suffix"]


def _qg_pair(d: Any) -> tuple[str, str]:
    """Extract (query, ground_truth) from a Sample or legacy dict."""
    if hasattr(d, "query"):
        return d.query, d.ground_truth
    return d.get("query", ""), d.get("ground_truth", "")


# Tuning keys — excluded from cycle identity in experiment mode.
#
# These control *how* the optimizer runs, not *what problem* it solves.
# In experiment mode (default), changing any of these between runs does NOT
# create a new cycle — cached candidates and dataset_run results carry over.
#
# In strict mode (for publication), all params are included in the hash so
# any deviation creates a distinct experiment for reproducibility.
TUNING_KEYS = frozenset(
    {
        # Loop control — how long/aggressively the loop runs
        "max_rounds",
        "l1_patience",
        "l2_patience",
        "l3_patience",
        "degradation_threshold",
        # Optimization strategy — tweakable between runs
        "model",
        "n_variants",
        "creativity",
        "improvement_threshold",
        "sp_budget_ttest",
        "seed",
    }
)


def cycle_config_identity(
    config: CampaignConfig,
    baseline_rendered: str,
    dataset: list,
    active_steps: list[str],
    *,
    strict: bool = False,
) -> str:
    """Stable identity hash for a feedback cycle configuration.

    Experiment mode (default, ``strict=False``) hashes only the *problem*
    (active pipeline steps + baseline prompt + dataset).  Everything in
    ``TUNING_KEYS`` is excluded, so tweaking optimizer strategy, resuming,
    or changing model between runs does not create a new cycle.  Strict
    mode hashes every parameter for publication reproducibility.

    ``active_steps`` is passed explicitly — keeps ``domain/`` free of
    imports from ``application/`` (``Session`` owns the pipeline schema).
    """
    opt = config.optimization
    payload_dict: dict[str, Any] = {
        "max_rounds": opt.max_rounds,
        "l1_patience": opt.l1_patience,
        "n_variants": opt.n_variants,
        "creativity": opt.creativity,
        "improvement_threshold": opt.improvement_threshold,
        "model": config.optimizer_llm.model,
        "sp_budget_ttest": config.sp_budget_ttest,
        "seed": opt.seed,
        "l2_patience": opt.l2_patience,
        "l3_patience": opt.l3_patience,
        "degradation_threshold": opt.degradation_threshold,
        "active_steps": list(active_steps),
        "baseline_rendered": baseline_rendered,
        "dataset_pairs": sorted(_qg_pair(d) for d in dataset),
    }
    if not strict:
        for k in TUNING_KEYS:
            payload_dict.pop(k, None)
    payload = json.dumps(payload_dict, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"


def cycle_hash_suffix(
    config: CampaignConfig,
    baseline_rendered: str,
    dataset: list,
    active_steps: list[str],
    *,
    strict: bool = False,
) -> str:
    """Return the 12-hex content hash without the ``cycle_`` prefix."""
    return cycle_config_identity(
        config, baseline_rendered, dataset, active_steps, strict=strict
    ).removeprefix("cycle_")
