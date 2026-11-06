"""THE refusal value: what every door serves for a `shared/errors.py::PotterError` — REST's
`main.py::ErrorEnvelope`, MCP's `tools/call` error result and the terminal — each only rendering it."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from promptpotter.domain.account_words import refusal_words
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import PotterError


class Refusal(FrozenModel):
    """A refusal as served: the rest of the answer it rides in serves on beside it."""

    code: str = Field(description="The code a client branches on: the refusal's kind.")
    message: str = Field(
        description="The sentence an operator reads, naming its remedy and the Account tab it "
        "lives in, where it names one."
    )
    details: dict[str, Any] = Field(description="The refusal's facts, by name.")

    @classmethod
    def of(cls, refused: PotterError) -> Refusal:
        return cls(code=refused.code, message=refusal_words(refused), details=refused.details)
