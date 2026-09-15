"""The project's Pydantic base, where an unknown key is an error, plus its two wire scalars. Ruff resolves
``BaseModel`` per file, so a subclass here loses RUF012's exemption: write ``Field(default_factory=list)``."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

__all__ = ["StrictModel", "WireFloat", "WireInt"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_bool(v: object) -> object:
    """``bool`` IS an ``int`` in Python and Pydantic coerces it, so a wire ``true`` arrives as 1 —
    which reads as "disarm" on a count and as a $1 ceiling on a budget."""
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


# The two wire scalar types. `strict` is what refuses `true` where an int is meant; `allow_inf_nan`
# is what refuses `+inf`, which PASSES a bare `ge=` bound and then disarms the `BudgetGate` whose
# probe is `spent >= cap`. Neither is a default — both were bought.
WireInt = Annotated[int, Field(strict=True)]
WireFloat = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]
