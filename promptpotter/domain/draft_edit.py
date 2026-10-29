from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from promptpotter.domain.campaign_config import ArmGateMode, OptimizationConfig
from promptpotter.domain.pipeline_schema import ManifestNodeOverlay, ParamIntent
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel

DEFAULT_BUDGET_ROUNDS = 5
"""Matches the M10 prompt-iteration framework default."""

MAX_EDITED_ROUNDS = 100


class OptimizationOverrides(StrictModel):
    """The ``budget.rounds`` bound gates the operator EDIT path only; the trusted internal
    ``draft_from_dataset`` builds the dict directly, so a reused dataset may exceed it."""

    budget: Budget = Field(
        Budget(rounds=DEFAULT_BUDGET_ROUNDS),
        description=OptimizationConfig.model_fields["budget"].description,
    )
    halt_at_accuracy: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description=OptimizationConfig.model_fields["halt_at_accuracy"].description,
    )
    optimizer: str = Field(
        OptimizationConfig.model_fields["optimizer"].default,
        min_length=1,
        description=OptimizationConfig.model_fields["optimizer"].description,
    )
    nodes: dict[str, ManifestNodeOverlay] = Field(
        default_factory=dict,
        description=OptimizationConfig.model_fields["nodes"].description,
    )
    arm_gate: ArmGateMode = Field(
        OptimizationConfig.model_fields["arm_gate"].default,
        description=OptimizationConfig.model_fields["arm_gate"].description,
    )

    @field_validator("budget")
    @classmethod
    def _rounds_on_the_edit_path(cls, budget: Budget) -> Budget:
        if budget.rounds is not None and budget.rounds > MAX_EDITED_ROUNDS:
            raise ValueError(f"budget.rounds is at most {MAX_EDITED_ROUNDS} on a check-in")
        return budget


class NodeOutputEdit(StrictModel):
    """One node's authored output contract: the schema whole, and the field carrying the answer
    where the operator named one."""

    node: str = Field(min_length=1)
    output_schema: dict[str, Any]
    answer_field: str | None = None


class EditDraftPatch(StrictModel):
    """Sparse mutation payload — only declared fields ride through. The one origin-edit vocabulary
    every ingress shares; the rules that apply one are ``draft_patch.py``'s, and stay out of this
    module so a command payload naming the type loads no resolver."""

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    scoring_matcher: str | None = Field(default=None, min_length=1, max_length=64)
    # The empty string clears the dials back to correctness alone.
    scoring_dials: str | None = Field(default=None, max_length=512)
    raw_task_description: str | None = Field(default=None, min_length=1, max_length=16384)
    pipeline_overlay: dict[str, Any] | None = None
    node_narrowing: dict[str, list[ParamIntent]] | None = None
    node_output: NodeOutputEdit | None = None
    pipeline_steps: list[str] | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Per step, its fields by name; replaces the draft's whole map.
    origin_prompts: dict[str, dict[str, str]] | None = None
    # Merged onto the draft's current overrides, `nodes` key by key.
    optimization_overrides: dict[str, Any] | None = None
    candidate_library: list[str] | None = Field(default=None, min_length=1)


SETTABLE_SCALARS: frozenset[str] = frozenset(
    name for name, f in EditDraftPatch.model_fields.items() if f.annotation == (str | None)
)
