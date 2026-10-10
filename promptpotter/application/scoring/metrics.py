from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from promptpotter.application.scoring.evaluators import materialize_round_values
from promptpotter.application.scoring.formula import cell_channels_of
from promptpotter.application.scoring.selection import level_band
from promptpotter.domain.results import CellFold, ScoreSummary
from promptpotter.domain.results_health import is_deprecated
from promptpotter.domain.scoring import ROW_GRADES

if TYPE_CHECKING:
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import CellSheet, GradedCell

__all__ = [
    "INVALID_SCORES",
    "compute_composite_fitness",
    "fold_cells",
]


# The registry's COMPLEMENT: `fitness`/`errored`/`degraded` are already reported as rates.
_MEANED_CHANNELS: tuple[str, ...] = ("latency", "cost", "tokens")


def _channel_means(scoreable: Sequence[GradedCell]) -> dict[str, float]:
    """Over the rows that ANSWERED each channel: the full count reports the round as cheaper than it was."""
    sums: dict[str, list[float]] = {}
    for cell in scoreable:
        answered = cell_channels_of(cell.facts, cell.grade.fitness)
        for name in _MEANED_CHANNELS:
            if (value := answered.get(name)) is not None:
                sums.setdefault(name, []).append(value)
    return {name: sum(vs) / len(vs) for name, vs in sums.items()}


def fold_cells(sheet: CellSheet) -> CellFold:
    accuracy, _, _ = level_band(sheet, ROW_GRADES["fitness"])
    composite, _, _ = level_band(sheet, ROW_GRADES["objective"])
    return CellFold(
        total=len(sheet.scoreable),
        accuracy=accuracy,
        composite_fitness=composite,
        deprecated=sum(1 for cell in sheet if is_deprecated(cell.facts)),
    )


def compute_composite_fitness(
    sheet: CellSheet,
    pipeline_schema: PipelineSchema,
) -> ScoreSummary:
    """The MEAN of each cell's worth, folded and never re-scored: the same per-cell value θ is fit on."""
    evaluator_values = materialize_round_values(pipeline_schema, sheet.cells)
    # A reading of the round, never a formula input: f(mean) is not mean(f).
    evaluator_values.update(_channel_means(sheet.scoreable))
    _, ci_lo, ci_hi = level_band(sheet, ROW_GRADES["fitness"])
    return ScoreSummary(
        **fold_cells(sheet).model_dump(),
        evaluators=evaluator_values,
        mean_fitness_ci_lo=ci_lo,
        mean_fitness_ci_hi=ci_hi,
    )


INVALID_SCORES = ScoreSummary(
    total=0,
    accuracy=None,
    composite_fitness=None,
    deprecated=0,
    evaluators={},
    mean_fitness_ci_lo=None,
    mean_fitness_ci_hi=None,
)
