"""Cycle-identity hashing — decides whether two runs share a feedback cycle.

See ``docs/architecture/optimization.md § Cycle Identity`` for the full
two-tier (experiment vs strict) specification.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.application.campaign.config import LoopConfig

__all__ = ["TUNING_KEYS", "cycle_config_identity"]


# Tuning keys — excluded from cycle identity in experiment mode.
#
# These control *how* the optimizer runs, not *what problem* it solves.
# In experiment mode (default), changing any of these between runs does NOT
# create a new cycle — cached candidates and dataset_run results carry over.
#
# In strict mode (for publication), all params are included in the hash so
# any deviation creates a distinct experiment for reproducibility.
#
# Also used by resume logic to hot-update stored configs on existing cycles.
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
    config: LoopConfig,
    baseline_rendered: str,
    dataset: list[dict],
    *,
    strict: bool = False,
) -> str:
    """Stable identity hash for a feedback cycle configuration.

    Experiment mode (default, ``strict=False``) hashes only the *problem*
    (active pipeline steps + baseline prompt + dataset).  Everything in
    ``TUNING_KEYS`` is excluded, so tweaking optimizer strategy, resuming,
    or changing model between runs does not create a new cycle.  Strict
    mode hashes every parameter for publication reproducibility.

    Infrastructure fields (``backend_url``, ``project_root``, …) are
    always excluded.
    """
    payload_dict: dict[str, Any] = {
        "max_rounds": config.max_rounds,
        "l1_patience": config.l1_patience,
        "n_variants": config.n_variants,
        "creativity": config.creativity,
        "improvement_threshold": config.improvement_threshold,
        "model": config.model,
        "sp_budget_ttest": config.sp_budget_ttest,
        "seed": config.seed,
        "l2_patience": config.l2_patience,
        "l3_patience": config.l3_patience,
        "degradation_threshold": config.degradation_threshold,
        "active_steps": list(config.pipeline_schema.active_steps) if config.pipeline_schema else [],
        "baseline_rendered": baseline_rendered,
        "dataset_pairs": sorted((d.get("query", ""), d.get("ground_truth", "")) for d in dataset),
    }
    if not strict:
        for k in TUNING_KEYS:
            payload_dict.pop(k, None)
    payload = json.dumps(payload_dict, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
    return f"cycle_{digest}"
