from __future__ import annotations

from typing import NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.spend import BudgetChange, SpendCeilings
from promptpotter.domain.strict_model import StrictModel, WireFloat, WireInt

__all__ = ["HeldLimits", "LaunchLimits"]


class LaunchLimits(StrictModel):
    """What a launch gesture ASKS: the accuracy a run halts at and the budgets it declares, bounded
    once for every ingress that declares any. Never what the run holds — that is :class:`HeldLimits`,
    a different type so the one cannot be passed where the other is meant."""

    model_config = ConfigDict(frozen=True)

    halt_at_accuracy: WireFloat | None = Field(default=None, ge=0.0, le=1.0)
    # Both budget arms ride: the USD one goes blind on a model with no rate on file and the token
    # one is what still holds, so serving only USD is a half-gate.
    spend_budget_usd: WireFloat | None = Field(default=None, ge=0.0)
    token_budget: WireInt | None = Field(default=None, ge=0)

    @property
    def budgets(self) -> BudgetChange:
        return BudgetChange(self.spend_budget_usd, self.token_budget)


class HeldLimits(NamedTuple):
    """What a run HOLDS once its declaration is admitted: the ONE ceiling that is reserved on the
    job, set on the run's config, stamped on the dashboard and armed on the spend book.

    ``operator`` is the subset of arms an operator gesture declared (a launch flag, a standing
    ``set-budget``), at their HELD values — the runner persists exactly those as the cycle's
    standing ceiling, so a raise outlives the launch that made it while a knob nobody touched keeps
    coming from the config."""

    halt_at_accuracy: float | None
    ceiling: SpendCeilings
    operator: BudgetChange

    @classmethod
    def admitted(
        cls, requested: LaunchLimits, ceiling: SpendCeilings, operator: BudgetChange
    ) -> HeldLimits:
        """*ceiling* as the run holds it. Only the arms an operator set are theirs to persist, at
        the value held for them — never the value asked for, which admission may have clamped."""
        return cls(
            halt_at_accuracy=requested.halt_at_accuracy,
            ceiling=ceiling,
            operator=BudgetChange(
                None if operator.usd is None else ceiling.usd,
                None if operator.tokens is None else ceiling.tokens,
            ),
        )
