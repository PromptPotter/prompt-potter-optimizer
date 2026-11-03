"""Why a dataset cannot be started HERE: one reason the listing serves and every opening refuses with."""

from __future__ import annotations

from enum import StrEnum

from promptpotter.shared.errors import PayloadInvalidError


class StartBlock(StrEnum):
    """Why a check-in cannot open a dataset here, as `START_BLOCK_WORDS` words it."""

    VERIFIER_GRADED = "verifier_graded"
    EMPTY = "empty"
    MISSING_EXTRA = "missing_extra"
    UNREADABLE = "unreadable"
    BACKEND_UNREACHABLE = "backend_unreachable"


START_BLOCK_WORDS: dict[StartBlock, str] = {
    StartBlock.VERIFIER_GRADED: "Each sample is graded by running a verifier rather than by a "
    "labelled answer, so it is launched from the command line, not from here.",
    StartBlock.EMPTY: "It holds no row with both a query and an answer.",
    StartBlock.MISSING_EXTRA: "It runs through a Python extra this install lacks.",
    StartBlock.UNREADABLE: "Its samples could not be read on this install.",
    StartBlock.BACKEND_UNREACHABLE: "Its backend is not answering; it opens once the backend is up.",
}
assert START_BLOCK_WORDS.keys() == set(StartBlock)


class DatasetUnstartableError(PayloadInvalidError):
    code = "dataset_unstartable"

    def __init__(self, name: str, block: StartBlock) -> None:
        super().__init__(f"{name}: {START_BLOCK_WORDS[block]}", details={"reason": block.value})
