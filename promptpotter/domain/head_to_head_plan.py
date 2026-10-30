from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator

from promptpotter.domain.command_kinds import (
    ArmRequest,
    HeadToHeadRequest,
    MintHeadToHeadPayload,
)
from promptpotter.domain.draft_edit import OptimizationOverrides
from promptpotter.domain.spend import Budget
from promptpotter.domain.strict_model import RequestBody

_Key = Annotated[str, Field(min_length=1, max_length=15, pattern=r"^[A-Za-z0-9_]+$")]


class HeadToHeadPlan(RequestBody):
    """A head-to-head declared before it is minted: each budget point is one `mint-head-to-head`
    of every arm × replicate, the budget being what its arms share."""

    head_to_head_id: str = Field(
        min_length=1,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Each budget point mints `{head_to_head_id}-{point}`.",
    )
    dataset_name: str = Field(min_length=1, max_length=64)
    arms: dict[_Key, OptimizationOverrides] = Field(
        min_length=2,
        description="Each arm's optimizer and its knobs over the dataset's own, as `head-to-head "
        "--arm` lays them; never a budget.",
    )
    replicates: int = Field(
        1,
        ge=1,
        le=9,
        description="Campaigns per arm, keyed `{arm}-s{n}`: one treatment, so the head-to-head's "
        "matrix reads them as one arm's replicates.",
    )
    budgets: dict[_Key, Budget] = Field(
        min_length=1, description="The budget points, each one head-to-head's `budget`."
    )

    @field_validator("arms")
    @classmethod
    def _arms_as_asked(
        cls, arms: dict[str, OptimizationOverrides]
    ) -> dict[str, OptimizationOverrides]:
        for key, knobs in arms.items():
            ArmRequest(arm_key=key, optimization=knobs)
        return arms

    def heads(self) -> list[MintHeadToHeadPayload]:
        return [
            MintHeadToHeadPayload(
                dataset_name=self.dataset_name,
                head_to_head=HeadToHeadRequest(
                    head_to_head_id=f"{self.head_to_head_id}-{label}",
                    arms=[
                        ArmRequest(arm_key=f"{arm}-s{n}", optimization=knobs)
                        for arm, knobs in self.arms.items()
                        for n in range(1, self.replicates + 1)
                    ],
                    budget=point,
                ),
            )
            for label, point in self.budgets.items()
        ]
