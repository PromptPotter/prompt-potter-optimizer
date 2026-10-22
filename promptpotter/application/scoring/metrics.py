from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from promptpotter.application.scoring.evaluators import materialize_round_values
from promptpotter.application.scoring.formula import cell_channels_of
from promptpotter.application.scoring.selection import level_band
from promptpotter.domain.results import CellFold, ScoreSummary, arm_end, recall_at
from promptpotter.domain.results_health import is_degraded, is_deprecated
from promptpotter.domain.scoring import ROW_GRADES
from promptpotter.domain.spend import TokenAccount

if TYPE_CHECKING:
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.results import ArmState
    from promptpotter.domain.scoring import CellSheet, GradedCell

__all__ = [
    "arm_folds",
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
        degraded=sum(1 for cell in sheet if is_degraded(cell.facts)),
        recall_at=recall_at(sheet),
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
        walked=len(sheet.cells),
        cached=sum(1 for cell in sheet if cell.facts.cached),
        evaluators=evaluator_values,
        mean_fitness_ci_lo=ci_lo,
        mean_fitness_ci_hi=ci_hi,
        tokens=TokenAccount.from_measured_rows(cell.facts for cell in sheet),
    )


def arm_folds(
    arms: Mapping[ArmKey, ArmState], cells: Mapping[ArmKey, CellSheet], schema: PipelineSchema
) -> dict[ArmKey, ScoreSummary]:
    """THE fold of a round's arms, each over the cells the round files for it."""
    return {
        key: compute_composite_fitness(cells[key], schema)
        for key, arm in arms.items()
        if arm_end(arm) != "invalid"
    }
