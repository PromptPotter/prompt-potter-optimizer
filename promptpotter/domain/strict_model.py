"""Ruff resolves ``BaseModel`` per file, so a subclass loses RUF012's exemption: write ``Field(default_factory=list)``."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class StrictModel(BaseModel):
    # A validator builds on first use, so an import pays for no model the process never reads.
    model_config = ConfigDict(extra="forbid", defer_build=True)


class FrozenModel(StrictModel):
    model_config = ConfigDict(frozen=True)


class RequestBody(StrictModel):
    """A route's body (`main.py::_request_bodies`)."""

    # Built at once: FastAPI builds a body's validator under a warning filter that a deferred build escapes.
    model_config = ConfigDict(defer_build=False)


def _reject_bool(v: object) -> object:
    """Pydantic coerces a wire ``true`` to 1, which reads as "disarm" on a count and a $1 ceiling on a budget."""
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


WireInt = Annotated[int, Field(strict=True)]
# `allow_inf_nan=False` refuses `+inf`, which PASSES a bare `ge=` bound and disarms a `spent >= cap` ceiling.
# The bound sits before the validator: after it, pydantic's JSON Schema writes a non-standard `ge`.
NonNegativeWireFloat = Annotated[
    float, Field(ge=0.0, allow_inf_nan=False), BeforeValidator(_reject_bool)
]
PositiveWireFloat = Annotated[
    float, Field(gt=0.0, allow_inf_nan=False), BeforeValidator(_reject_bool)
]
