from __future__ import annotations

from typing import NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain.spend import BudgetChange, SpendCeilings
from promptpotter.domain.strict_model import StrictModel, WireFloat, WireInt
from promptpotter.shared.errors import ConflictError

__all__ = ["HeldLimits", "LaunchLimits", "RoundsCap", "refuse_arm_halt"]


class RoundsCap(StrictModel):
    """An operator's L1 round cap on one cycle; ``max_rounds`` ``None`` lifts it, so the spend
    ceiling governs. Where a cap may be absent, ``RoundsCap | None`` — ``None`` never moved it."""

    model_config = ConfigDict(frozen=True)

    max_rounds: WireInt | None = Field(ge=0)


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


def refuse_arm_halt(halt_at_accuracy: float | None) -> None:
    """An arm stops on its budget alone, so a launch naming a halt accuracy is refused: at the
    mint, before anything exists, and again where a resume holds its limits."""
    if halt_at_accuracy is not None:
        raise ConflictError(
            "an arm stops on its budget, never on an accuracy: drop the halt accuracy",
            code="arm_halt_refused",
        )


class HeldLimits(NamedTuple):
    """What a run HOLDS once its declaration is admitted: the ``ceiling`` it stops on — set on
    the run's config, stamped on the dashboard, armed on the spend book — and the ``reserve`` its
    bills may never pass, which is the job's reservation and ``None`` on an arm no account bounds.

    ``operator`` is the subset of arms an operator gesture declared (a launch flag, a standing
    ``set-limits``), at their HELD values — the runner persists exactly those as the cycle's
    standing ceiling, so a raise outlives the launch that made it while a knob nobody touched keeps
    coming from the config."""

    halt_at_accuracy: float | None
    ceiling: SpendCeilings
    operator: BudgetChange
    reserve: SpendCeilings

    @classmethod
    def admitted(
        cls,
        requested: LaunchLimits,
        ceiling: SpendCeilings,
        operator: BudgetChange,
        *,
        reserve: SpendCeilings,
    ) -> HeldLimits:
        """*ceiling* as the run holds it. Only the arms an operator set are theirs to persist, at
        the value held for them — never the value asked for, which admission may have clamped."""
        return cls(
            halt_at_accuracy=requested.halt_at_accuracy,
            ceiling=ceiling,
            reserve=reserve,
            operator=BudgetChange(
                None if operator.usd is None else ceiling.usd,
                None if operator.tokens is None else ceiling.tokens,
            ),
        )
