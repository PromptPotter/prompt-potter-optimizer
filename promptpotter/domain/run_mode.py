from __future__ import annotations

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel, WireInt

__all__ = ["RunMode"]


class RunMode(StrictModel):
    """The SHAPE a run takes, on the wire and in the runner alike. How far it goes is its ``Budget``."""

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
