from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from promptpotter.domain.campaign_config import OptimizationConfig, OptimizerChoice
from promptpotter.domain.pipeline_schema import ParamIntent
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel

MAX_EDITED_ROUNDS = 100


class OptimizationOverrides(OptimizerChoice):
    """An ``optimization`` an operator sends: a check-in edit's, a mint's, a head-to-head arm's.
    Only the fields sent move anything, so no field states a default a reader could take for one
    that applies. Its ``budget.rounds`` bound is theirs alone; a dataset's own block is never read
    through it, so a reused dataset may exceed it."""

    budget: Budget = Field(
        default_factory=Budget,
        description=OptimizationConfig.model_fields["budget"].description,
    )
    halt_at_accuracy: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description=OptimizationConfig.model_fields["halt_at_accuracy"].description,
    )

    @field_validator("budget")
    @classmethod
    def _rounds_on_the_edit_path(cls, budget: Budget) -> Budget:
        if budget.rounds is not None and budget.rounds > MAX_EDITED_ROUNDS:
            raise ValueError(f"budget.rounds is at most {MAX_EDITED_ROUNDS} on a check-in")
        return budget


class NodeOutputEdit(StrictModel):
    """One node's authored output contract: the schema whole, and the fields carrying the answer
    where the operator named them."""

    node: str = Field(min_length=1)
    output_schema: dict[str, Any]
    answer_fields: list[str] | None = None


class EditDraftPatch(StrictModel):
    """Sparse mutation payload — only declared fields ride through. The one origin-edit vocabulary
    every ingress shares; the rules that apply one are ``draft_patch.py``'s, and stay out of this
    module so a command payload naming the type loads no resolver."""

    slug: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    connector: str | None = Field(default=None, min_length=1, max_length=64)
    # A blank task is no task: the draft holds none until one is set.
    raw_task_description: str | None = Field(default=None, max_length=16384, pattern=r"\S")
    config: dict[str, Any] | None = Field(
        default=None,
        description="A partial `CampaignConfig`, by its own paths, laid over the draft's by "
        "`campaign_config.py::merge_config_layers`; its `optimization` reads as an "
        "`OptimizationOverrides`. A string `scoring` names the criterion whole; a `scoring` "
        "mapping is folded into the criterion the draft runs, key by key: `matcher` picks "
        "the per-sample matcher, `dials` the composite's weights (the empty string clears them), "
        "`per_sample` and `per_cell` set those formulas.",
    )
    node_narrowing: dict[str, list[ParamIntent]] | None = None
    node_output: NodeOutputEdit | None = None
    column_query: str | None = Field(default=None, max_length=256)
    column_ground_truth: str | None = Field(default=None, max_length=256)
    # Per step, its fields by name; replaces the draft's whole map.
    origin_prompts: dict[str, dict[str, str]] | None = None
    candidate_library: list[str] | None = Field(default=None, min_length=1)


SETTABLE_SCALARS: frozenset[str] = frozenset(
    name for name, f in EditDraftPatch.model_fields.items() if f.annotation == (str | None)
)
