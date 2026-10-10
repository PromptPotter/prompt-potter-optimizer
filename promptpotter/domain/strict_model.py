"""Ruff resolves ``BaseModel`` per file, so a subclass loses RUF012's exemption: write ``Field(default_factory=list)``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

__all__ = ["StrictModel", "WireFloat", "WireInt"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def stored_field_model(cls, key: str, raw: Mapping[str, object]) -> type[BaseModel] | None:
        return None


def _reject_bool(v: object) -> object:
    """Pydantic coerces a wire ``true`` to 1, which reads as "disarm" on a count and a $1 ceiling on a budget."""
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


# `allow_inf_nan=False` refuses `+inf`, which PASSES a bare `ge=` bound and disarms a `spent >= cap` ceiling.
WireInt = Annotated[int, Field(strict=True)]
WireFloat = Annotated[float, BeforeValidator(_reject_bool), Field(allow_inf_nan=False)]
