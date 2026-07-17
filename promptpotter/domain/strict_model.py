"""The project's Pydantic base — unknown keys are an error, not a shrug.

Pydantic's own default is ``extra="ignore"``: a model built with a misspelled
keyword silently drops it and returns a valid-looking instance. That cost us
``ObservationMapping(obs_key=…)`` — the field is ``output_field``, so the
mapping was a no-op for months with every gate green.

Inherit :class:`StrictModel` and the posture is decided by default. Declaring
``extra`` is still allowed and now means something: it is a written-down
choice, not an unexamined inherited default. ``model_config`` merges across
inheritance, so a subclass adds ``frozen=True`` without restating ``extra``.

Models that must tolerate unknown keys (``Sample``, ``Settings``) say so
explicitly. The ledger's ``models_lax`` dimension counts the ones that don't
end up strict, so the exceptions stay countable.

One cost, paid knowingly: ruff resolves ``BaseModel`` only within a file, so
inheriting across one costs RUF012's Pydantic exemption — a mutable field
default (``tags: list[str] = []``) is flagged here where it would not be on a
direct ``BaseModel`` subclass. Write ``Field(default_factory=list)``; it is
already this package's dominant idiom, so the lint agrees with the house style
rather than fighting it.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["StrictModel"]


class StrictModel(BaseModel):
    """A :class:`~pydantic.BaseModel` that rejects unknown keys."""

    model_config = ConfigDict(extra="forbid")
