from __future__ import annotations

from promptpotter.domain.pass_kinds import PASSES
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.scoring import MeasuredCell
from promptpotter.shared.measurement_context import MeasurementRole

MEASUREMENT_GRADES = ("A", "B", "C")


REUSABLE_MIN_GRADE = "B"
"""Floor for serving a banked answer back as a cache hit: every consumer excludes ``C`` (decisions.md § 0005)."""

_GRADE_RANK = {grade: rank for rank, grade in enumerate(reversed(MEASUREMENT_GRADES), start=1)}


def _ran_llm_path(measurement: MeasuredCell, schema: PipelineSchema | None) -> bool:
    """``terminal_node`` names the deepest node that RAN; an unstamped row is credited, never penalised."""
    terminal_node = measurement.pipeline.terminal_node
    if terminal_node is None:
        return True
    return schema is not None and any(n.is_llm and n.name == terminal_node for n in schema.nodes)


def grade_answer(
    kind: MeasurementRole,
    measurement: MeasuredCell,
    schema: PipelineSchema | None,
    *,
    human_intervened: bool,
) -> str:
    if human_intervened:
        return "C"
    met = int(PASSES[kind].deliberate) + int(_ran_llm_path(measurement, schema))
    return "CBA"[met]


def meets_grade(grade: str, min_grade: str) -> bool:
    return _GRADE_RANK.get(grade, 0) >= _GRADE_RANK.get(min_grade, 0)
