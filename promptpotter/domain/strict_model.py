"""The project's Pydantic base — unknown keys are an error, not a shrug.

Why, and when a model may stay lax: ``domain/CLAUDE.md`` § Conventions. One
cost of the extra hop: ruff resolves ``BaseModel`` per file, so a subclass here
loses RUF012's Pydantic exemption — write ``Field(default_factory=list)``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["StrictModel"]


class StrictModel(BaseModel):
    """A :class:`~pydantic.BaseModel` that rejects unknown keys."""

    model_config = ConfigDict(extra="forbid")
