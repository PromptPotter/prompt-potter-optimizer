from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

from promptpotter.application.intelligence.rasch import (
    Observation,
    fit_rasch,
    responses_of,
    theta_cells,
)
from promptpotter.application.optimizers.nodes import RaceSnapshot
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.application.scoring.selection import discordant
from promptpotter.domain.cycle_paths import Lens
from promptpotter.domain.paired_reading import ArmKey, PriorStanding
from promptpotter.domain.ruler import DeltaRuler
from promptpotter.domain.scoring import NO_CELLS, CellSheet, Grade, GradedCell, MeasuredCell
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.worded import Worded

# θ draws per field member, enough that a p_best near ε reads the same across seeds.
_DRAWS = 4000


class EliminationGate(StrEnum):
    """Named by the producer, never re-derived from which keys a context carries."""

    EPSILON = "epsilon"  # measurement stopped, NOT a verdict


ABORT_LENS_SUPPRESS: dict[str, frozenset[str]] = {
    **{f"{g.value}_off": frozenset({g.value}) for g in EliminationGate},
    "all_off": frozenset(g.value for g in EliminationGate),
}

# The browser's picklist, keyed as the `lens=` it sends; exactly what `mask/tree_lens.py` accepts.
ABORT_LENS_LABELS: Worded[str, str] = Worded(
    {
        Lens("abort", f"{EliminationGate.EPSILON.value}_off").spelling: "No ε-elimination",
        Lens("abort", "all_off").spelling: "No early abort",
    }
)
assert set(ABORT_LENS_LABELS) == {Lens("abort", body).spelling for body in ABORT_LENS_SUPPRESS}, (
    "abort-lens vocabulary drift between ABORT_LENS_LABELS and ABORT_LENS_SUPPRESS"
)


class EliminationContext(TypedDict, total=False):
    """``gate`` says which keys EXIST: ε adds the ε(n) that fired."""

    gate: EliminationGate
    p_best: float
    epsilon: float
    leader: ArmKey
    queries_scored: int
    total_queries: int
    n_priors: int


@dataclass(frozen=True)
class PoBBStop(StopSignal):
    """``priors`` is what a replay re-fits: each prior's scored cells as the race read them."""

    check_result: EliminationContext
    cells: list[str]
    priors: dict[ArmKey, CellSheet]
    paired_breakdown: dict[ArmKey, PriorStanding]


def race_sheet(cells: Iterable[GradedCell]) -> CellSheet:
    """An arm's rows as the race holds them; never filed, so it carries no scorer."""
    return CellSheet("", tuple(cells))


def graded_sheet(grades: Mapping[str, Mapping[str, object]]) -> CellSheet:
    """A replay's sheet off the grades a stop recorded (`PoBBStop.priors`, `wire_sheet`)."""
    return race_sheet(
        GradedCell(MeasuredCell.from_wire({"sample_key": k}), Grade.from_wire(g))
        for k, g in grades.items()
    )


def wire_sheet(sheet: CellSheet) -> dict[str, dict[str, float | None]]:
    # What θ and the sign cap read of a cell, never its answer.
    return {
        cell.key: {"fitness": cell.grade.fitness, "objective": cell.grade.objective}
        for cell in sheet.scoreable
    }


def field_p_best(
    field: Mapping[ArmKey, CellSheet], cells: Collection[str]
) -> tuple[dict[ArmKey, float], dict[ArmKey, NDArray[np.float64]]]:
    """Each member's posterior probability of being the field's best on *cells*: one hierarchical
    Rasch fit of the field, θ and δ together, and a Monte Carlo over its θ draws, seeded by the read
    so a replay draws the same. The shares sum to 1."""
    fit = fit_rasch(
        [
            Observation(key, cell, y)
            for key in field
            for cell, y in responses_of(field[key]).items()
            if cell in cells
        ]
    )
    if not fit.theta:
        return {}, {}
    rng = np.random.default_rng(len(cells))
    draws = {
        ArmKey(key): rng.normal(fit.theta[key], fit.theta_se[key], _DRAWS) for key in fit.theta
    }
    best = np.argmax(np.vstack(list(draws.values())), axis=0)
    shares = np.bincount(best, minlength=len(draws)) / _DRAWS
    return dict(zip(draws, map(float, shares), strict=True)), draws


class PoBBCheck:
    """A family-wise p_best on PoBB's joint posterior; ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        knobs: PoBBKnobs,
        *,
        n_min: int,
        n_samples: int,
        ruler: DeltaRuler | None,
        parent: ArmKey,
        on_snapshot: Callable[[RaceSnapshot], None] | None = None,
    ) -> None:
        # The SAME δ scale the round-winner election reads; ``None`` ⇒ flat, the ruler still cold.
        self.ruler = ruler
        self.parent = parent
        self.n_min = n_min
        self.epsilon = knobs.epsilon
        self.epsilon_floor = knobs.epsilon_floor
        self.epsilon_stop = knobs.epsilon_stop
        self.n_samples = n_samples
        # The GRADED rows, never a binarized hit, degenerate on a graded backend.
        self.priors: dict[ArmKey, CellSheet] = {}
        self._on_snapshot = on_snapshot

    def register_completed(self, results: Iterable[GradedCell], *, key: ArmKey) -> None:
        self.priors[key] = race_sheet(results)

    def extend_prior(self, key: ArmKey, rows: Iterable[GradedCell]) -> None:
        self.priors[key] = self.priors[key].merged(race_sheet(rows))

    def epsilon_at(self, n: int) -> float:
        if self.epsilon <= self.epsilon_floor:
            return self.epsilon
        scale = min(1.0, max(0.0, (n - self.n_min) / max(self.n_min, 1)))
        return self.epsilon_floor + (self.epsilon - self.epsilon_floor) * scale

    def check(
        self, field: Mapping[ArmKey, CellSheet], standing: Collection[ArmKey]
    ) -> dict[ArmKey, PoBBStop]:
        """An arm is cut when its posterior P(best of parent and every arm standing) falls below ε(n)."""
        racing = [key for key in standing if key != self.parent]
        if not racing:
            return {}
        shared = set.intersection(*({c.key for c in field[key].scoreable} for key in racing))
        walked = [c.key for c in field[racing[0]].scoreable if c.key in shared]
        parent = [c.key for c in field.get(self.parent, NO_CELLS).scoreable]
        # Every posterior gate and the ε ramp read the depth the election's coverage floor counts.
        cells = list(theta_cells(dict.fromkeys(walked), parent, self.ruler))
        if len(cells) < self.n_min:
            return {}
        p_best, draws = field_p_best(field, cells)
        bar = self.epsilon_at(len(cells))
        stops: dict[ArmKey, PoBBStop] = {}
        for key in (key for key in racing if key in draws):
            rivals = {pid: field[pid] for pid in draws if pid != key}
            paired_breakdown = {
                pid: PriorStanding(
                    p_better=float(np.mean(draws[key] > draws[pid])),
                    n_paired=len(cells),
                    n_discordant=sum(discordant(field[key], rival)),
                )
                for pid, rival in rivals.items()
            }
            n = len(field[key].cells)
            if self._on_snapshot is not None:
                self._on_snapshot(RaceSnapshot(p_best[key], key, n, paired_breakdown))
            if not self.epsilon_stop or p_best[key] >= bar:
                continue
            stops[key] = PoBBStop(
                self.name,
                "eliminated",
                {
                    "p_best": p_best[key],
                    "leader": max(rivals, key=p_best.__getitem__),
                    "queries_scored": n,
                    "total_queries": self.n_samples,
                    "n_priors": len(rivals),
                    "gate": EliminationGate.EPSILON,
                    "epsilon": float(bar),
                },
                cells=cells,
                priors=rivals,
                paired_breakdown=paired_breakdown,
            )
        return stops
