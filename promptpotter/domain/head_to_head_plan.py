from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator

from promptpotter.domain.campaign import Arm, Compare
from promptpotter.domain.command_kinds import MintCampaignPayload
from promptpotter.domain.draft_edit import OptimizationOverrides
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import StrictModel

_Key = Annotated[str, Field(min_length=1, max_length=15, pattern=r"^[A-Za-z0-9_]+$")]


class HeadToHeadPlan(StrictModel):
    """A head-to-head declared before it is minted: each budget point × arm × replicate is one
    `mint-campaign`, and a point is a head-to-head of its own, the budget being what arms share."""

    head_to_head_id: str = Field(
        min_length=1,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Each budget point mints `{head_to_head_id}-{point}`.",
    )
    dataset_name: str = Field(min_length=1, max_length=64)
    arms: dict[_Key, OptimizationOverrides] = Field(
        min_length=2,
        description="Each arm's knobs over the dataset's own, as `new --set` lays them; never a "
        "budget.",
    )
    replicates: int = Field(
        1,
        ge=1,
        le=9,
        description="Campaigns per arm, keyed `{arm}-s{n}`: one treatment, so the head-to-head's "
        "matrix reads them as one arm's replicates.",
    )
    budgets: dict[_Key, Budget] = Field(
        min_length=1, description="The budget points, each laid on every arm's `budget`."
    )

    @field_validator("arms")
    @classmethod
    def _shared_budget(
        cls, arms: dict[str, OptimizationOverrides]
    ) -> dict[str, OptimizationOverrides]:
        if budgeted := sorted(
            key for key, knobs in arms.items() if "budget" in knobs.model_fields_set
        ):
            raise ValueError(
                f"arms {budgeted} set a budget: a budget point is what every arm of a "
                "head-to-head shares"
            )
        return arms

    def mints(self) -> list[MintCampaignPayload]:
        return [
            MintCampaignPayload(
                dataset_name=self.dataset_name,
                # A point naming no axis is the dataset's own budget, which lays nothing.
                optimization=OptimizationOverrides.model_validate(
                    knobs.model_dump(mode="json", exclude_unset=True)
                    | ({"budget": point.model_dump(mode="json")} if point.named else {})
                ),
                purpose=Compare(
                    arm=Arm(
                        head_to_head_id=f"{self.head_to_head_id}-{label}", arm_key=f"{arm}-s{n}"
                    )
                ),
            )
            for label, point in self.budgets.items()
            for arm, knobs in self.arms.items()
            for n in range(1, self.replicates + 1)
        ]
