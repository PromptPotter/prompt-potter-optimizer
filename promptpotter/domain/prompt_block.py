"""A block pack: snippets, each with its provenance; a measured lift is never a field here, the archive derives it per model and dataset."""

from __future__ import annotations

import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from promptpotter.domain.opt_search_point import PROMPT_FIELDS
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt, stable_hash

# One path segment, so a name a knob carries resolves to a directory under the packs root alone.
PackName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*$")]

# The source an authored block is grouped under, which a pool's `active` path may name.
AUTHORED_SOURCE = "authored"

# The pack an account's own edits bank into, named alike in every account: a `packs` knob reads its run's.
ACCOUNT_PACK = "account"


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
    # The draft or campaign it was written in.
    experiment: str
    # One past the version of the snippet it supersedes, a cited one counting 0.
    version: Annotated[int, Field(ge=1)]
    # The content id of the snippet it was edited from; None for one written from nothing.
    supersedes: str | None
    notes: str | None


class PromptPart(StrEnum):
    """A prompt field a snippet suits, one name for a target and an optimizer prompt alike: a drop elsewhere is shown, never refused."""

    PERSONA = "persona"
    TASK_INTENT = "task_intent"
    PROBLEM_DESCRIPTION = "problem_description"
    INSTRUCTION = "instruction"
    THINKING_STYLE = "thinking_style"
    ANSWER_FORMAT = "answer_format"


assert tuple(PromptPart) == PROMPT_FIELDS, "a snippet suits exactly the six prompt fields"


@shapes_optimizer_prompt
def snippet_id(text: str) -> str:
    """A snippet's id is its content, the suffix of every pool entry it expands into."""
    return stable_hash(text, length=10)


class Snippet(FrozenModel):
    text: Annotated[str, StringConstraints(min_length=1)]
    # The parts it is drawn into, one pool entry each; at least one, or no pool ever holds it.
    suits: Annotated[frozenset[PromptPart], Field(min_length=1)]
    # Free labels for a library search alone: no pool, prompt or identity reads them.
    tags: tuple[str, ...]
    presumes: frozenset[Presumption] = frozenset()
    provenance: Annotated[Cited | Authored, Field(discriminator="kind")]

    @property
    def source(self) -> str:
        return self.provenance.work if isinstance(self.provenance, Cited) else AUTHORED_SOURCE

    @property
    def parts(self) -> tuple[PromptPart, ...]:
        return tuple(part for part in PromptPart if part in self.suits)

    @property
    def version(self) -> int:
        return self.provenance.version if isinstance(self.provenance, Authored) else 0


class BlockPack(FrozenModel):
    works: dict[str, Work]
    snippets: tuple[Snippet, ...]

    @model_validator(mode="after")
    def _cites_its_works(self) -> BlockPack:
        cited = {
            work
            for snippet in self.snippets
            if isinstance(snippet.provenance, Cited)
            for work in (snippet.provenance.work, *snippet.provenance.via)
        }
        if missing := cited - self.works.keys():
            raise ValueError(f"snippets cite works the pack does not declare: {sorted(missing)}")
        if uncited := self.works.keys() - cited:
            raise ValueError(f"the pack declares works no snippet cites: {sorted(uncited)}")
        texts = [snippet.text for snippet in self.snippets]
        if twice := sorted({snippet_id(t) for t in texts if texts.count(t) > 1}):
            raise ValueError(f"the pack holds snippets {twice} twice: one text is one snippet")
        return self
