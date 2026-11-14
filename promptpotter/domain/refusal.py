"""THE refusal value: what every door serves for a `shared/errors.py::PotterError` (REST's
`main.py::ErrorEnvelope`, MCP's error data and `tools/call` error result, the terminal's `--json`),
each door only rendering it, and what a store holds beside what it serves."""

from __future__ import annotations

from typing import Any, Self

from pydantic import Field

from promptpotter.domain.account_words import refusal_words
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import NextAct, PotterError


class Refusal(FrozenModel):
    """A refusal as served: the rest of the answer it rides in serves on beside it."""

    code: str = Field(description="The code a client branches on: the refusal's kind.")
    message: str = Field(
        description="The sentence an operator reads, naming its remedy and the Account tab it "
        "lives in, where it names one; never a verb or a flag, which its acts carry."
    )
    details: dict[str, Any] = Field(description="The refusal's facts, by name.")
    next_acts: tuple[NextAct, ...] = Field(
        default=(),
        description="The acts past it, as a stop's: each door spells one its own way (the "
        "terminal's verb and flag, the browser's control, the MCP tool its kind names).",
    )

    @classmethod
    def of(cls, refused: PotterError) -> Self:
        return cls(
            code=refused.code,
            message=refusal_words(refused),
            details=refused.details,
            next_acts=refused.next_acts,
        )
