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

ADR-8: OptSearchPoint is mutable (not frozen). Unlike SearchPoint which
is content-addressed (identity = hash), OptSearchPoint is a checkpoint
snapshot written once per round. Freezing adds no benefit and forces
unnecessary reconstruction. See M7 spec §13.2.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OptSearchPoint(BaseModel):
    """Optimizer-level search point — the optimizer's configuration at a moment.

    Persisted in trial checkpoints as ``opt_search_point``. Enables L4 to
    correlate optimizer configuration with target-pipeline evaluation outcomes.

    Mutable — updated in-place during the feedback cycle, then serialized
    via ``model_dump()`` at checkpoint time.
    """

    critique_text: str = ""
    critique: dict[str, Any] = Field(
        default_factory=dict,
        description="Full 5-field critique dict (positive_critique, negative_critique, "
        "priority_fix, suggested_axes, summary).",
    )
    thinking_styles: list[str] = Field(default_factory=list)
    plan: str = ""
    optimizer_params: dict[str, Any] = Field(default_factory=dict)
    task_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured domain context (domain, pipeline_purpose, "
        "data_characteristics, optimization_goals, key_challenges). "
        "Set from TASK_DESCRIPTION decomposition, refinable by L2.",
    )
    escalation_journal: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Cross-round degradation investigation memory. "
        "Tracks tried configs and their outcomes across escalation rounds.",
    )
    query_failure_tracker: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Per-query warning inventory across rounds. "
        "Keyed by query text, values are warning counters.",
    )
    l2_directive: str = Field(
        default="",
        description="L2's diagnostic reasoning + action guidance for L1. "
        "Sliding window of 1 — set after L2 runs, cleared when L2 doesn't fire.",
    )
    content_hashes: list[str] = Field(
        default_factory=list,
        description="Content hashes of dataset_runs produced under this config",
    )
