from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from promptpotter.domain.strict_model import FrozenModel, WireInt


class OnDivergence(StrEnum):
    """What a resume does where its replay departs the record: ONE answer, never two flags."""

    HALT = "halt"
    ACCEPT = "accept"
    FORK = "fork"


class RunMode(FrozenModel):
    """The SHAPE a run takes, on the wire and in the runner alike. How far it goes is its ``Budget``."""

    from_round: WireInt | None = Field(
        default=None,
        ge=0,
        description="Rewind in place to this round before running; unset continues the ledger.",
    )
    on_divergence: OnDivergence = Field(
        default=OnDivergence.HALT,
        description="Where the replay departs the record: `halt`, `accept` the departure and run "
        "on, or `fork` a sibling cycle there and run it.",
    )
