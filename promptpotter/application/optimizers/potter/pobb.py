from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

from promptpotter.application.intelligence.rasch import theta_cells
from promptpotter.application.optimizers.nodes import RaceSnapshot
from promptpotter.application.scoring.selection import (
    PairedGrade,
    PriorRead,
    capped_reach,
    cells_read,
    discordance,
    paired_grades,
    prior_read,
)
from promptpotter.domain.cycle_paths import Lens
from promptpotter.domain.paired_reading import PriorStanding
from promptpotter.domain.scoring import NO_CELLS, CellSheet, Grade, GradedCell, MeasuredCell
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.worded import Worded

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample


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


def prior_reads(
    sheet: CellSheet, priors: Mapping[ArmKey, CellSheet], ruler: DeltaRuler | None, *, n_min: int
) -> dict[ArmKey, PriorRead]:
    return {
        pid: read
        for pid, prior in priors.items()
        if (read := prior_read(sheet, prior, ruler, min_shared=n_min)) is not None
    }


class PoBBCheck:
    """Each prior is read on the candidate's exact cells; ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        knobs: PoBBKnobs,
        *,
        n_min: int,
        n_samples: int,
        ruler: DeltaRuler | None,
        parent: ArmKey,
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
        self._current: ArmKey | None = None
        self._on_snapshot: Callable[[RaceSnapshot], None] | None = None

    def set_current(
        self,
        key: ArmKey,
        on_snapshot: Callable[[RaceSnapshot], None] | None = None,
    ) -> None:
        self._current = key
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

    def check(self, results: Sequence[GradedCell]) -> StopSignal | None:
        n = len(results)
        if n < self.n_min:
            return None
        parent = self.priors.get(self.parent, NO_CELLS)
        # Every posterior gate and the ε ramp read the depth the election's coverage floor counts.
        read = cells_read("ability", results, [c.key for c in parent.scoreable], self.ruler)
        if read < self.n_min:
            return None
        sheet = race_sheet(results)
        reads = prior_reads(sheet, self.priors, self.ruler, n_min=self.n_min)
        if not reads:
            return None
        leader = min(reads, key=lambda k: reads[k].p_better)
        p_best = reads[leader].p_better
        paired_breakdown = {
            # The graded cells the fit read, never `n`, which counts errored rows too.
            pid: PriorStanding(p_better=r.p_better, n_paired=r.paired, n_discordant=r.discordant)
            for pid, r in reads.items()
        }

        if self._on_snapshot is not None and self._current is not None:
            self._on_snapshot(
                RaceSnapshot(
                    p_best=float(p_best),
                    current=self._current,
                    n_samples=n,
                    paired_breakdown=paired_breakdown,
                )
            )

        # ε is the ONLY gate; the priors include the round's parent (`potter/race.py`).
        bar = self.epsilon_at(read)
        if not self.epsilon_stop or p_best >= bar:
            return None
        return PoBBStop(
            self.name,
            "eliminated",
            {
                "p_best": float(p_best),
                "leader": leader,
                "queries_scored": n,
                "total_queries": self.n_samples,
                "n_priors": len(reads),
                "gate": EliminationGate.EPSILON,
                "epsilon": float(bar),
            },
            cells=[cell.key for cell in sheet.scoreable],
            priors=dict(self.priors),
            paired_breakdown=paired_breakdown,
        )

    def earliest_stop(
        self,
        results: Sequence[GradedCell],
        upcoming: Sequence[tuple[Sample, GradedCell | None]],
        *,
        unresolved: Mapping[ArmKey, Iterable[GradedCell]] | None = None,
    ) -> int | None:
        """An ``unresolved`` candidate may never become a prior: graded where its rows say, anything elsewhere, never counted on."""
        if not self.epsilon_stop:
            return None
        grades = paired_grades(cell for cell in results if cell.scored)
        cells = list(grades)
        # A prior may become one: an arm ahead still walking.
        priors = {pid: paired_grades(sheet) for pid, sheet in self.priors.items()} | {
            pid: paired_grades(rows)
            for pid, rows in (unresolved or {}).items()
            if pid not in self.priors
        }
        for m, (sample, row) in enumerate(upcoming, start=len(results) + 1):
            if row is None or row.scored:
                cells.append(sample.key)
            if row is not None and row.scored:
                grades.update(paired_grades([row]))
            if m < self.n_min:
                continue
            # Cold, a catch-up may yet give the parent any cell, and a depth below what `check` will
            # read lands the horizon past its stop: so it counts the most it could read.
            read = len(theta_cells(dict.fromkeys(cells), cells, self.ruler))
            if not priors or read < self.n_min:
                continue
            # Whatever θ reads, the sign cap bounds the gate (`capped_reach`), an open cell either way.
            if any(
                capped_reach(*_reach(cells, grades, g))[0] < self.epsilon_at(read)
                for g in priors.values()
            ):
                return m
        return None


_UNSOLVED = PairedGrade(0.0, 0.0)
_SOLVED = PairedGrade(1.0, 1.0)


def _reach(
    cells: Sequence[str],
    candidate: Mapping[str, PairedGrade],
    prior: Mapping[str, PairedGrade],
) -> tuple[int, int]:
    """The candidate's discordant (wins, losses) over *prior* on *cells*, every cell either lacks
    read as a loss: the least support the sign cap can give."""
    return discordance(
        [candidate.get(s, _UNSOLVED) for s in cells], [prior.get(s, _SOLVED) for s in cells]
    )
