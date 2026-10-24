"""A measured lift is never a field here: it is derived from the archive, per model and dataset."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from promptpotter.domain.strict_model import FrozenModel


class Presumption(StrEnum):
    """What a block's text takes for granted of the run it lands in; a run lacking one makes it noise."""

    # Candidates to rank: some node of the chain emits a ranking.
    RANKING = "ranking"
    # The reply's last line is read as its answer: the cell's matcher extracts it there.
    LAST_LINE_ANSWER = "last_line_answer"


class PromptBlock(FrozenModel):
    id: str
    text: str
    source: str
    level: Literal["target", "optimizer"] | None = None
    role: str | None = None
    presumes: frozenset[Presumption] = frozenset()
    technique_family: str | None = None
    source_paper: str | None = None
    source_url: str | None = None
    source_locator: str | None = None
    year: int | None = None
    evolved_or_authored: Literal["evolved", "authored", "selected"] | None = None
    evolved_on_model: str | None = None
    evaluated_on_models: tuple[str, ...] | None = None
    task_family: str | None = None
    benchmark: str | None = None
    baseline: str | None = None
    reported_score: float | None = None
    reported_lift: float | None = None
    verbatim: bool | None = None
    notes: str | None = None
