"""The project's Pydantic base — unknown keys are an error, not a shrug. One cost of the extra hop: ruff resolves
``BaseModel`` per file, so a subclass here loses RUF012's exemption — write ``Field(default_factory=list)``."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["StrictModel"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
