"""OptSearchPoint — a point in the optimizer's own search space.

Parallel to ``SearchPoint`` (target pipeline parameter bundle).
Where ``SearchPoint`` captures *what prompt/config to evaluate*,
``OptSearchPoint`` captures *what the optimizer was configured to do*
at a specific moment in the feedback cycle.

L4 meta-optimization searches over OptSearchPoints, just as L1-L3
search over SearchPoints.

Cross-reference design: OptSearchPoint holds ``content_hashes`` linking
to target-layer ``dataset_runs`` produced under this optimizer config.
Target data stays clean — all provenance lives in the optimizer layer.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OptSearchPoint(BaseModel):
    """Optimizer-level search point — the optimizer's configuration at a moment.

    Persisted in trial checkpoints as ``opt_search_point``. Enables L4 to
    correlate optimizer configuration with target-pipeline evaluation outcomes.
    """

    model_config = {"frozen": True}

    critique_text: str = ""
    thinking_styles: list[str] = Field(default_factory=list)
    plan: str = ""
    context: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    content_hashes: list[str] = Field(
        default_factory=list,
        description="Content hashes of dataset_runs produced under this config",
    )
