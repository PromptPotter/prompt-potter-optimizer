"""A block pack: prompt blocks, each with its provenance; a measured lift is never a field here, the archive derives it per model and dataset."""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from promptpotter.domain.strict_model import FrozenModel

# One path segment, so a name a knob carries resolves to a directory under the packs root alone.
PackName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*$")]

# The source an authored block is grouped under, which a pool's `active` path may name.
AUTHORED_SOURCE = "authored"


class Presumption(StrEnum):
    """What a block's text takes for granted of the run it lands in; a run lacking one makes it noise."""

    # Candidates to rank: some node of the chain emits a ranking.
    RANKING = "ranking"
    # The reply's last line is read as its answer: the cell's matcher extracts it there.
    LAST_LINE_ANSWER = "last_line_answer"


class Work(FrozenModel):
    first_author: str
    year: int
    title: str
    license: str


class Reported(FrozenModel):
    # In the work's own units as it prints them; a lift it does not print stays None.
    task_family: str | None
    benchmark: str | None
    evaluated_on_models: tuple[str, ...]
    score: float | None
    lift: float | None
    baseline: str | None


class Cited(FrozenModel):
    kind: Literal["cited"]
    # A key of the pack's `works`, an arXiv id; it is the block's source.
    work: str
    locator: str
    verbatim: bool
    # The works the text reached the pack through, nearest last.
    via: tuple[str, ...]
    technique_family: str
    role: str
    evolved_or_authored: Literal["evolved", "authored", "selected"]
    evolved_on_model: str | None
    reported: Reported
    notes: str | None


class Authored(FrozenModel):
    kind: Literal["authored"]
    author: str
    date: datetime.date
    experiment: str
    version: str
    notes: str | None


class PromptBlock(FrozenModel):
    text: Annotated[str, StringConstraints(min_length=1)]
    presumes: frozenset[Presumption] = frozenset()
    provenance: Annotated[Cited | Authored, Field(discriminator="kind")]

    @property
    def source(self) -> str:
        return self.provenance.work if isinstance(self.provenance, Cited) else AUTHORED_SOURCE


class BlockPack(FrozenModel):
    works: dict[str, Work]
    prompt_fields: dict[str, tuple[PromptBlock, ...]]

    @model_validator(mode="after")
    def _cites_its_works(self) -> BlockPack:
        cited = {
            work
            for blocks in self.prompt_fields.values()
            for block in blocks
            if isinstance(block.provenance, Cited)
            for work in (block.provenance.work, *block.provenance.via)
        }
        if missing := cited - self.works.keys():
            raise ValueError(f"blocks cite works the pack does not declare: {sorted(missing)}")
        if uncited := self.works.keys() - cited:
            raise ValueError(f"the pack declares works no block cites: {sorted(uncited)}")
        return self
