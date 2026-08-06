"""Provenance grade (``A`` > ``B`` > ``C``) from ``source`` + per-sample ``terminated_at``, stamped once at
``build_dataset_run_data``. Consumers read the grade instead of being fooled by row count."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema

MEASUREMENT_GRADES = ("A", "B", "C")
"""Ordinal quality grades, best first."""

# Sources the optimizer stamps when it deliberately explores the search space.
# `origin` = round-0 origin scoring; `optimization_loop` = an L1/L2/L3 candidate;
# `feedback_cycle` = an operator-driven re-score. Anything else (a connector
# backfill, a degradation re-check, a row written outside the loop) is incidental.
# This is the quality-of-exploration taxonomy.
DELIBERATE_SOURCES = frozenset({"origin", "optimization_loop", "feedback_cycle"})

# Fraction of a run's samples that must have run the deliberate LLM path for the
# run to count as a real evaluation rather than a connector-retrieval batch.
LLM_PATH_FLOOR = 0.5

_GRADE_RANK = {grade: rank for rank, grade in enumerate(reversed(MEASUREMENT_GRADES), start=1)}


@dataclass(frozen=True, slots=True)
class RunProvenance:
    grade: str
    deliberate_source: bool
    llm_path_fraction: float
    human_intervened: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "deliberate_source": self.deliberate_source,
            "llm_path_fraction": round(self.llm_path_fraction, 4),
            "human_intervened": self.human_intervened,
        }


def is_deliberate_source(source: str) -> bool:
    return source in DELIBERATE_SOURCES


def llm_terminal_nodes(schema: PipelineSchema | None) -> frozenset[str]:
    """Names of the schema's LLM nodes — the nodes a deliberate evaluation ends at."""
    if schema is None:
        return frozenset()
    return frozenset(node.name for node in schema.nodes if node.is_llm)


def _ran_llm_path(measurement: Mapping[str, Any], llm_nodes: frozenset[str]) -> bool:
    """Whether one sample reached the deliberate LLM evaluation. An EMPTY ``terminated_at`` means the pipeline ran to
    completion; a set value naming a non-LLM node means it short-circuited before any LLM call."""
    pd = measurement.get("pipeline_data") or {}
    terminated_at = pd.get("terminated_at") or ""
    if not terminated_at:
        return True
    return terminated_at in llm_nodes


def llm_path_fraction(
    measurements: Iterable[Mapping[str, Any]],
    llm_nodes: frozenset[str],
) -> float:
    """Fraction of *measurements* that ran the deliberate LLM path (0.0 when empty)."""
    rows = list(measurements)
    if not rows:
        return 0.0
    ran = sum(1 for m in rows if _ran_llm_path(m, llm_nodes))
    return ran / len(rows)


def grade_run(
    source: str,
    measurements: Iterable[Mapping[str, Any]],
    schema: PipelineSchema | None,
    *,
    human_intervened: bool = False,
) -> RunProvenance:
    """``A`` deliberate source AND LLM path, ``B`` one of the two, ``C`` neither. A ``human_intervened`` run is forced to
    ``C`` regardless: deliberate, but no longer a clean autonomous datapoint."""
    deliberate = is_deliberate_source(source)
    frac = llm_path_fraction(measurements, llm_terminal_nodes(schema))
    full_path = frac >= LLM_PATH_FLOOR
    if human_intervened:
        grade = "C"
    elif deliberate and full_path:
        grade = "A"
    elif deliberate or full_path:
        grade = "B"
    else:
        grade = "C"
    return RunProvenance(
        grade=grade,
        deliberate_source=deliberate,
        llm_path_fraction=frac,
        human_intervened=human_intervened,
    )


def entry_grade(entry: Mapping[str, Any]) -> str:
    """Read a run summary's grade; unstamped rows grade ``C`` (treated as incidental)."""
    prov = entry.get("provenance")
    if isinstance(prov, Mapping):
        grade = prov.get("grade")
        if grade in _GRADE_RANK:
            return str(grade)
    return "C"


def meets_grade(grade: str, min_grade: str) -> bool:
    """True iff *grade* is at least as good as *min_grade* (``A`` ≥ ``B`` ≥ ``C``)."""
    return _GRADE_RANK.get(grade, 0) >= _GRADE_RANK.get(min_grade, 0)


__all__ = [
    "entry_grade",
    "grade_run",
    "meets_grade",
]
