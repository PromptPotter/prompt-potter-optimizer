from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from promptpotter.application.scoring.evaluators import materialize_round_values
from promptpotter.application.scoring.formula.compiler import cell_channels_of
from promptpotter.application.scoring.selection import level_band
from promptpotter.domain.arms import arm_end
from promptpotter.domain.cell_fold import CellFold, ScoreSummary, field_accuracy, recall_at
from promptpotter.domain.results_health import is_degraded, is_deprecated
from promptpotter.domain.scoring import ROW_GRADES
from promptpotter.domain.spend import TokenAccount

if TYPE_CHECKING:
    from promptpotter.domain.arms import ArmState
    from promptpotter.domain.paired_reading import ArmKey, MeasurandUnit
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import CellSheet, GradedCell, Scorer


# Meaned whatever the formula reads; `errored`/`degraded` are reported as rates.
_MEANED_CHANNELS: tuple[str, ...] = ("latency", "cost", "tokens")


def _channel_means(scoreable: Sequence[GradedCell], names: Iterable[str]) -> dict[str, float]:
    """Over the rows that ANSWERED each channel: the full count reports the round as cheaper than it was."""
    sums: dict[str, list[float]] = {}
    for cell in scoreable:
        answered = cell_channels_of(cell.facts, cell.grade)
        for name in names:
            if (value := answered.get(name)) is not None:
                sums.setdefault(name, []).append(value)
    return {name: sum(vs) / len(vs) for name, vs in sums.items()}


def fold_cells(sheet: CellSheet, fitness_unit: MeasurandUnit) -> CellFold:
    fitness, _, _ = level_band(sheet, ROW_GRADES["fitness"])
    composite, _, _ = level_band(sheet, ROW_GRADES["objective"])
    return CellFold(
        total=len(sheet.scoreable),
        fitness=fitness,
        fitness_unit=fitness_unit,
        composite_fitness=composite,
        deprecated=sum(1 for cell in sheet if is_deprecated(cell.facts)),
        degraded=sum(1 for cell in sheet if is_degraded(cell.facts)),
        recall_at=recall_at(sheet),
        field_accuracy=field_accuracy(sheet.scoreable),
    )


def compute_composite_fitness(
    sheet: CellSheet, scorer: Scorer, pipeline_schema: PipelineSchema
) -> ScoreSummary:
    """The MEAN of each cell's worth, folded and never re-scored: the same per-cell value θ is fit on."""
    fold = fold_cells(sheet, scorer.fitness_unit)
    evaluator_values = materialize_round_values(pipeline_schema, sheet.cells)
    # A reading of the round, never a formula input: f(mean) is not mean(f). `fitness` is the fold's.
    terms = scorer.terms - {"fitness"}
    evaluator_values.update(_channel_means(sheet.scoreable, {*_MEANED_CHANNELS, *terms}))
    if fold.fitness is not None:
        evaluator_values["fitness"] = fold.fitness
    _, ci_lo, ci_hi = level_band(sheet, ROW_GRADES["fitness"])
    return ScoreSummary(
        **fold.model_dump(),
        walked=len(sheet.cells),
        cached=sum(1 for cell in sheet if cell.facts.cached),
        evaluators=evaluator_values,
        fitness_terms=sorted(terms & evaluator_values.keys()),
        mean_fitness_ci_lo=ci_lo,
        mean_fitness_ci_hi=ci_hi,
        tokens=TokenAccount.from_measured_rows(cell.facts for cell in sheet),
    )


def arm_folds(
    arms: Mapping[ArmKey, ArmState],
    cells: Mapping[ArmKey, CellSheet],
    scorer: Scorer,
    schema: PipelineSchema,
) -> dict[ArmKey, ScoreSummary]:
    """THE fold of a round's arms, each over the cells the round files for it."""
    return {
        key: compute_composite_fitness(cells[key], scorer, schema)
        for key, arm in arms.items()
        if arm_end(arm) != "invalid"
    }
