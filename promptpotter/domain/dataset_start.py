"""Why a dataset cannot be started HERE: one reason the listing serves and every opening refuses with."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import PayloadInvalidError


class StartBlock(StrEnum):
    VERIFIER_GRADED = "verifier_graded"
    EMPTY = "empty"
    MISSING_EXTRA = "missing_extra"
    UNREADABLE = "unreadable"


class DatasetStartBlock(StrictModel):
    reason: StartBlock = Field(description="Why a check-in cannot open this dataset here.")
    explanation: str = Field(description="The reason, as a sentence for the person choosing.")


class DatasetUnstartableError(PayloadInvalidError):
    code = "dataset_unstartable"

    def __init__(self, name: str, block: DatasetStartBlock) -> None:
        super().__init__(f"{name}: {block.explanation}", details={"reason": block.reason.value})
        self.block = block


__all__ = ["DatasetStartBlock", "DatasetUnstartableError", "StartBlock"]
