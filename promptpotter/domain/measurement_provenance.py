"""Measurement provenance grade — how deliberate and comparable a run is.

Every archived run already carries the two signals that separate a
deliberately-explored datapoint from an incidental one: ``source`` (who
produced the run) and, per sample, ``pipeline_data.terminated_at`` (which node
the pipeline ended at). Reuse (``MeasurementArchive.load_reusable_results``) and
the ``AxisIndex`` digest historically ignored both, grouping by config-prefix +
raw row count — so cheap connector short-circuits, which never ran the
deliberate LLM evaluation, outnumbered and biased the few good explored points.
That is why the cross-cycle digest the L1/L2/L3 prompts read drifts toward
connector noise, and why reuse had to be switched off wholesale.

This module turns those existing signals into one ordinal grade
(``A`` > ``B`` > ``C``) stamped on each run at the single write path
(``build_dataset_run_data``) and carried on the index summary. Consumers read
the grade instead of being fooled by row count: the digest drops grade ``C``,
reuse can filter to deliberate runs, and a future optimizer-in-an-optimizer (L4)
ingests only graded-clean records. One concept, three consumers.
"""

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
# backfill, a degradation re-check, an unstamped legacy row) is incidental.
# This is the quality-of-exploration taxonomy; it is deliberately separate from
# `tracing.replay.classify_run_origin`, which buckets the same field for Langfuse
# trace grouping — different consumer, different purpose.
DELIBERATE_SOURCES = frozenset({"origin", "optimization_loop", "feedback_cycle"})

# Fraction of a run's samples that must have run the deliberate LLM path for the
# run to count as a real evaluation rather than a connector-retrieval batch.
LLM_PATH_FLOOR = 0.5

_GRADE_RANK = {grade: rank for rank, grade in enumerate(reversed(MEASUREMENT_GRADES), start=1)}


@dataclass(frozen=True, slots=True)
class RunProvenance:
    """The provenance verdict for one archived run."""

    grade: str
    deliberate_source: bool
    llm_path_fraction: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "deliberate_source": self.deliberate_source,
            "llm_path_fraction": round(self.llm_path_fraction, 4),
        }


def is_deliberate_source(source: str) -> bool:
    """True iff *source* marks the run as deliberate optimizer exploration."""
    return source in DELIBERATE_SOURCES


def llm_terminal_nodes(schema: PipelineSchema | None) -> frozenset[str]:
    """Names of the schema's LLM nodes — the nodes a deliberate evaluation ends at."""
    if schema is None:
        return frozenset()
    return frozenset(node.name for node in schema.nodes if node.is_llm)


def _ran_llm_path(measurement: Mapping[str, Any], llm_nodes: frozenset[str]) -> bool:
    """Whether one sample reached the deliberate LLM evaluation.

    An empty ``terminated_at`` means the pipeline ran to completion (its terminal
    node is the LLM step, per ``rendering._terminal_node``); a set value naming a
    non-LLM node means the sample short-circuited in a connector/cache/retrieval
    step before any LLM call.
    """
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
) -> RunProvenance:
    """Grade a run from its source + per-sample termination.

    ``A`` — deliberate source *and* the batch ran the LLM path (a clean explored
    datapoint). ``B`` — one of the two but not both (a deliberate batch that
    mostly short-circuited, or a full LLM batch from an incidental source). ``C``
    — neither: an incidental connector-retrieval replay, the bias source.
    """
    deliberate = is_deliberate_source(source)
    frac = llm_path_fraction(measurements, llm_terminal_nodes(schema))
    full_path = frac >= LLM_PATH_FLOOR
    if deliberate and full_path:
        grade = "A"
    elif deliberate or full_path:
        grade = "B"
    else:
        grade = "C"
    return RunProvenance(grade=grade, deliberate_source=deliberate, llm_path_fraction=frac)


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
    "DELIBERATE_SOURCES",
    "LLM_PATH_FLOOR",
    "MEASUREMENT_GRADES",
    "RunProvenance",
    "entry_grade",
    "grade_run",
    "is_deliberate_source",
    "llm_path_fraction",
    "llm_terminal_nodes",
    "meets_grade",
]
