from __future__ import annotations

import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import MeasuredCell

MEASUREMENT_GRADES = ("A", "B", "C")


class RunSource(enum.StrEnum):
    ORIGIN = "origin"
    OPTIMIZATION_LOOP = "optimization_loop"
    VERIFY = "verify"
    AB = "ab"
    NOISE_FLOOR = "noise_floor"
    SEED_SCREEN = "seed_screen"
    DECISION_BANK = "decision_bank"

    @property
    def deliberate(self) -> bool:
        match self:
            case RunSource.ORIGIN | RunSource.OPTIMIZATION_LOOP:
                return True
            case (
                RunSource.VERIFY
                | RunSource.AB
                | RunSource.NOISE_FLOOR
                | RunSource.SEED_SCREEN
                | RunSource.DECISION_BANK
            ):
                return False


REUSABLE_MIN_GRADE = "B"
"""Floor for serving a banked answer back as a cache hit: every consumer excludes ``C`` (ADR-0005)."""

_GRADE_RANK = {grade: rank for rank, grade in enumerate(reversed(MEASUREMENT_GRADES), start=1)}


def _ran_llm_path(measurement: MeasuredCell, schema: PipelineSchema | None) -> bool:
    """``terminal_node`` names the deepest node that RAN; an unstamped row is credited, never penalised."""
    terminal_node = measurement.pipeline.terminal_node
    if terminal_node is None:
        return True
    return schema is not None and any(n.is_llm and n.name == terminal_node for n in schema.nodes)


def grade_answer(
    source: RunSource,
    measurement: MeasuredCell,
    schema: PipelineSchema | None,
    *,
    human_intervened: bool,
) -> str:
    if human_intervened:
        return "C"
    met = int(source.deliberate) + int(_ran_llm_path(measurement, schema))
    return "CBA"[met]


def meets_grade(grade: str, min_grade: str) -> bool:
    return _GRADE_RANK.get(grade, 0) >= _GRADE_RANK.get(min_grade, 0)


__all__ = [
    "REUSABLE_MIN_GRADE",
    "RunSource",
    "grade_answer",
    "meets_grade",
]
