from __future__ import annotations

from collections.abc import Iterable

from promptpotter.domain.scoring import GradedCell
from promptpotter.domain.spend import TokenAccount
from promptpotter.domain.strict_model import FrozenModel
from promptpotter.shared.hashing import shapes_optimizer_prompt


class CellFold(FrozenModel):
    # The cells carrying a verdict, a deprecated one included as a miss.
    total: int
    # ``None`` where `total` is 0: no fitness is not a 0.0.
    accuracy: float | None
    composite_fitness: float | None
    deprecated: int
    degraded: int
    # :func:`recall_at`; empty where the formula reads no ranking.
    recall_at: dict[int, float]


class ScoreSummary(CellFold):
    """An arm's fold of its cells: what it walked, and its level on them."""

    # Every cell taken, errored and deprecated ones included; ``cached`` of them replayed.
    walked: int
    cached: int
    evaluators: dict[str, float]
    # Student-t CI on the mean per-cell FITNESS, whatever the formula; ``None`` under two cells.
    mean_fitness_ci_lo: float | None
    mean_fitness_ci_hi: float | None
    # BACKEND tokens only, replayed rows excluded: judge and optimizer spend carry no arm.
    tokens: TokenAccount | None


def unscoreable_cells(results: Iterable[GradedCell]) -> int:
    """Never ``walked - total``, which counts a deprecated row as a hole."""
    return sum(1 for r in results if r.facts.errored)


def recall_at(results: Iterable[GradedCell]) -> dict[int, float]:
    answered = [r.grade for r in results if not r.facts.errored]
    if all(grade.n_candidates is None for grade in answered):
        return {}
    ranks = [grade.ground_truth_rank for grade in answered]
    return {
        k: sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks) for k in (1, 5)
    }


@shapes_optimizer_prompt
def merge_known_outcomes(
    prior: Iterable[GradedCell], incoming: Iterable[GradedCell]
) -> list[GradedCell]:
    """Never score this pool: its rows were measured by DIFFERENT configurations."""
    by_key = {cell.key: cell for cell in prior}
    for cell in incoming:
        by_key[cell.key] = cell
    return list(by_key.values())


@shapes_optimizer_prompt
def measured_cells(rows: Iterable[GradedCell]) -> set[str]:
    return {cell.key for cell in rows if cell.scored}


def is_floor_pinned(rows: Iterable[GradedCell]) -> bool:
    """``fitness``, never ``objective``: a ``per_cell`` composite charges a miss, so all-miss is not 0."""
    graded = [
        fitness for cell in rows if cell.scored and (fitness := cell.grade.fitness) is not None
    ]
    return bool(graded) and all(fitness <= 0.0 for fitness in graded)
