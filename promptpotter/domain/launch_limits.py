from __future__ import annotations

from pydantic import ConfigDict, Field

from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.strict_model import StrictModel, WireFloat, WireInt
from promptpotter.shared.errors import ConflictError

__all__ = ["HeldLimits", "LaunchLimits", "RoundsCap", "RunMode", "refuse_arm_halt"]


class RunMode(StrictModel):
    """The SHAPE a run takes, on the wire and in the runner alike. How far it goes is a limit."""

    model_config = ConfigDict(frozen=True)

    from_round: WireInt | None = Field(
        default=None,
        ge=0,
        description="Rewind in place to this round before running; unset continues the ledger.",
    )
    no_divergence_check: bool = Field(
        default=False, description="Accept a replay that diverges from the record."
    )
    fork_on_divergence: bool = Field(
        default=False, description="Branch a sibling cycle where the replay diverges, and run it."
    )
    diag: bool = Field(
        default=False,
        description="The diagnostic shape; a `start-run` of a cycle that finished one runs a "
        "counted sibling.",
    )


class RoundsCap(StrictModel):
    """``max_rounds`` ``None`` LIFTS the cap, so the spend ceiling governs; an absent cap never moved it."""

    model_config = ConfigDict(frozen=True)

    max_rounds: WireInt | None = Field(ge=0)


class LaunchLimits(StrictModel):
    """What a launch ASKS, never what the run holds: ``HeldLimits`` is a different type on purpose."""

    model_config = ConfigDict(frozen=True)

    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    # Both arms ride: the USD one goes blind on a model with no rate on file, and the token one still holds.
    ceiling: SpendCeilings = SpendCeilings()
    step_rounds: WireInt | None = Field(default=None, ge=1, le=100)


def refuse_arm_halt(halt_at_accuracy: float | None) -> None:
    if halt_at_accuracy is not None:
        raise ConflictError(
            "an arm stops on its budget, never on an accuracy: drop the halt accuracy",
            code="arm_halt_refused",
        )


class HeldLimits(StrictModel):
    """``operator`` holds only the arms a gesture declared: the runner persists exactly those as standing."""

    model_config = ConfigDict(frozen=True)

    halt_at_accuracy: float | None
    ceiling: SpendCeilings
    operator: SpendCeilings
    reserve: SpendCeilings
    step_rounds: int | None

    @classmethod
    def admitted(
        cls,
        requested: LaunchLimits,
        ceiling: SpendCeilings,
        operator: SpendCeilings,
        *,
        reserve: SpendCeilings,
    ) -> HeldLimits:
        """The operator's arms at the value HELD for them, never the one asked for, which admission may have clamped."""
        return cls(
            halt_at_accuracy=requested.halt_at_accuracy,
            ceiling=ceiling,
            reserve=reserve,
            step_rounds=requested.step_rounds,
            operator=SpendCeilings(
                None if operator.usd is None else ceiling.usd,
                None if operator.tokens is None else ceiling.tokens,
            ),
        )
