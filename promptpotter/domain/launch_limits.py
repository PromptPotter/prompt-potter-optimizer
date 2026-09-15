from __future__ import annotations

from pydantic import ConfigDict, Field

from promptpotter.domain.spend import SpendCeilings
from promptpotter.domain.strict_model import StrictModel, WireFloat, WireInt

__all__ = ["LaunchLimits"]


class LaunchLimits(StrictModel):
    """The accuracy a run halts at and the budgets it may spend, bounded once for every ingress
    that declares any — a launch ASKS with one and admission hands back the one it HOLDS."""

    model_config = ConfigDict(frozen=True)

    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    # Both budget arms ride: the USD one goes blind on a model with no rate on file and the token
    # one is what still holds, so serving only USD is a half-gate.
    spend_budget_usd: WireFloat | None = Field(default=None, ge=0.0)
    token_budget: WireInt | None = Field(default=None, ge=0)

    @property
    def budgets(self) -> SpendCeilings:
        return SpendCeilings(self.spend_budget_usd, self.token_budget)

    def holding(self, admitted: SpendCeilings) -> LaunchLimits:
        return self.model_copy(
            update={"spend_budget_usd": admitted.usd, "token_budget": admitted.tokens}
        )
