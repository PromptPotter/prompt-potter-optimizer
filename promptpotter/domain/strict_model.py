"""Ruff resolves ``BaseModel`` per file, so a subclass loses RUF012's exemption: write ``Field(default_factory=list)``."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Annotated, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class StrictModel(BaseModel):
    # A validator builds on first use, so an import pays for no model the process never reads.
    model_config = ConfigDict(extra="forbid", defer_build=True)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        """A copy with *update* is VALIDATED, as a read is: an unknown key or a state a validator
        refuses raises. Only the update's keys join what was set."""
        if not update:
            return super().model_copy(deep=deep)
        if self.model_config.get("extra") != "allow" and (
            unknown := update.keys() - type(self).model_fields.keys()
        ):
            raise ValueError(f"{type(self).__name__} has no field {sorted(unknown)}")
        held = copy.deepcopy(dict(self)) if deep else dict(self)
        copied = self.model_validate({**held, **update}, by_name=True)
        object.__setattr__(copied, "__pydantic_fields_set__", self.model_fields_set | update.keys())
        if self.__pydantic_private__ is not None:
            object.__setattr__(copied, "__pydantic_private__", dict(self.__pydantic_private__))
        return copied


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

# One file's bound at every door: each transport reads one byte past it, and the bytes' own
# validation refuses that byte (`IngestUploadPayload.blob`, `EditDraftPatch.candidate_library_file`).
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UploadBytes = Annotated[bytes, Field(max_length=MAX_UPLOAD_BYTES)]
