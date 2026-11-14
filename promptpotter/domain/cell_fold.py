from __future__ import annotations

from collections.abc import Iterable

from promptpotter.domain.paired_reading import Estimate, MeasurandUnit
from promptpotter.domain.scoring import GradedCell
from promptpotter.domain.spend import TokenAccount
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.errors import ErrorCategory


class CellFold(FrozenModel):
    # The cells carrying a verdict, a deprecated one included as a miss.
    total: int
    # ``None`` where `total` is 0: no fitness is not a 0.0.
    fitness: float | None
    # The grading scorer's ``fitness_unit``: every printer of `fitness` reads it.
    fitness_unit: MeasurandUnit
    composite_fitness: float | None
    deprecated: int
    degraded: int
    # :func:`recall_at`; empty where the formula reads no ranking.
    recall_at: dict[int, float]
    # :func:`field_accuracy`; empty where the formula grades no fields.
    field_accuracy: dict[str, float]


class ScoreSummary(CellFold):
    """An arm's fold of its cells: what it walked, and its level on them."""

    # Every cell taken, errored and deprecated ones included; ``cached`` of them replayed.
    walked: int
    cached: int
    evaluators: dict[str, float]
    # The `evaluators` the scoring formula reads, `fitness` aside: each one's round mean.
    fitness_terms: list[str]
    # The mean per-cell FITNESS with its interval, whatever the formula; ``None`` where `fitness` is.
    fitness_band: Estimate | None
    # BACKEND tokens only, replayed rows excluded: judge and optimizer spend carry no arm.
    tokens: TokenAccount | None

    @property
    def term_means(self) -> dict[str, float]:
        return {name: self.evaluators[name] for name in self.fitness_terms}


def unscoreable_cells(results: Iterable[GradedCell]) -> int:
    """Never ``walked - total``, which counts a deprecated row as a hole."""
    return sum(1 for r in results if r.facts.errored)


def repairable_holes(results: Iterable[GradedCell]) -> int:
    """The unscoreable cells a re-walk can fill: ``HALTED`` is none, its declared bound cuts the
    next attempt at the same place."""
    return sum(
        1 for r in results if r.facts.errored and r.facts.error_category is not ErrorCategory.HALTED
    )


def recall_at(results: Iterable[GradedCell]) -> dict[int, float]:
    answered = [r.grade for r in results if not r.facts.errored]
    if all(grade.n_candidates is None for grade in answered):
        return {}
    ranks = [grade.ground_truth_rank for grade in answered]
    return {
        k: sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks) for k in (1, 5)
    }


def field_accuracy(scoreable: Iterable[GradedCell]) -> dict[str, float]:
    """Each field's share of the cells whose gold grades it; an errored cell graded none."""
    verdicts: dict[str, list[bool]] = {}
    for cell in scoreable:
        for name, hit in cell.grade.field_hits or ():
            verdicts.setdefault(name, []).append(hit)
    return {name: sum(hits) / len(hits) for name, hits in verdicts.items()}


def merge_known_outcomes(
    prior: Iterable[GradedCell], incoming: Iterable[GradedCell]
) -> list[GradedCell]:
    """Never score this pool: its rows were measured by DIFFERENT configurations."""
    by_key = {cell.key: cell for cell in prior}
    for cell in incoming:
        by_key[cell.key] = cell
    return list(by_key.values())


def measured_cells(rows: Iterable[GradedCell]) -> set[str]:
    return {cell.key for cell in rows if cell.scored}


def is_prior_dominated(rows: Iterable[GradedCell]) -> bool:
    """All-miss or all-hit, on ``fitness``, never ``objective``: a ``per_cell`` composite charges a
    miss, so all-miss is not 0."""
    graded = {
        fitness for cell in rows if cell.scored and (fitness := cell.grade.fitness) is not None
    }
    return bool(graded) and (max(graded) <= 0.0 or min(graded) >= 1.0)
