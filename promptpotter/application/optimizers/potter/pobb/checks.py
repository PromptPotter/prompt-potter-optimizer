from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

from promptpotter.application.intelligence.rasch import theta_cells
from promptpotter.application.optimizers.nodes import RaceSnapshot
from promptpotter.application.scoring.selection import (
    PairedGrade,
    PairedPosterior,
    cells_read,
    discordance,
    elimination_p_best_bounds,
    paired_grades,
    paired_p_best,
)
from promptpotter.domain.validators import StopSignal

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell


class EliminationGate(StrEnum):
    """Named by the producer, never re-derived from which keys a context carries."""

    EPSILON = "epsilon"  # measurement stopped, NOT a verdict
    LOCK_IN = "lock_in"


ABORT_LENS_LABELS: dict[str, str] = {
    f"{EliminationGate.EPSILON.value}_off": "No ε-elimination",
    f"{EliminationGate.LOCK_IN.value}_off": "No lock-in",
    "all_off": "No early abort",
}

ABORT_LENS_SUPPRESS: dict[str, frozenset[str]] = {
    **{f"{g.value}_off": frozenset({g.value}) for g in EliminationGate},
    "all_off": frozenset(g.value for g in EliminationGate),
}

# The browser's picklist is emitted from the labels and must be exactly what `mask/tree_lens.py` accepts.
assert set(ABORT_LENS_LABELS) == set(ABORT_LENS_SUPPRESS), (
    "abort-lens vocabulary drift: "
    f"unlabelled {sorted(set(ABORT_LENS_SUPPRESS) - set(ABORT_LENS_LABELS))}, "
    f"unserved {sorted(set(ABORT_LENS_LABELS) - set(ABORT_LENS_SUPPRESS))}"
)


class EliminationContext(TypedDict, total=False):
    """``gate`` says which keys EXIST: lock-in adds the admission bar it met, ε the ε(n) that fired."""

    gate: EliminationGate
    p_best: float
    epsilon: float
    admission: float
    leader: ArmKey
    queries_scored: int
    total_queries: int
    n_priors: int


@dataclass(frozen=True)
class PoBBStop(StopSignal):
    """``fit`` is the pairing a replay re-fits."""

    check_result: EliminationContext
    fit: PairedPosterior
    paired_breakdown: dict[ArmKey, dict[str, float]]


class PoBBCheck:
    """Each prior is read on the candidate's exact cells; ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        knobs: PoBBKnobs,
        *,
        admission: float | None,
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
        self.early_stop = knobs.early_stop
        # The selector's own promotion bar (`Selector.admission`); ``None`` locks no leader in.
        self.admission = admission
        self.n_samples = n_samples
        # The GRADED response and verdict, never a binarized hit, degenerate on a graded backend.
        self.priors_by_sample: dict[ArmKey, dict[str, PairedGrade]] = {}
        self._current: ArmKey | None = None
        self._on_snapshot: Callable[[RaceSnapshot], None] | None = None

    @property
    def prior_keys(self) -> list[ArmKey]:
        return list(self.priors_by_sample)

    def set_current(
        self,
        key: ArmKey,
        on_snapshot: Callable[[RaceSnapshot], None] | None = None,
    ) -> None:
        self._current = key
        self._on_snapshot = on_snapshot

    def register_completed(self, results: Iterable[GradedCell], *, key: ArmKey) -> None:
        self.priors_by_sample[key] = paired_grades(results)

    def extend_prior(self, key: ArmKey, rows: Iterable[GradedCell]) -> None:
        self.priors_by_sample[key].update(paired_grades(rows))

    def epsilon_at(self, n: int) -> float:
        if self.epsilon <= self.epsilon_floor:
            return self.epsilon
        scale = min(1.0, max(0.0, (n - self.n_min) / max(self.n_min, 1)))
        return self.epsilon_floor + (self.epsilon - self.epsilon_floor) * scale

    def check(self, results: Sequence[GradedCell]) -> StopSignal | None:
        n = len(results)
        if n < self.n_min:
            return None
        # Every posterior gate and the ε ramp read the depth the election's coverage floor counts.
        read = cells_read(
            "ability", results, self.priors_by_sample.get(self.parent, {}), self.ruler
        )
        if read < self.n_min:
            return None
        fit = paired_p_best(results, self.priors_by_sample, self.ruler, min_shared=self.n_min)
        if fit is None:
            return None
        leader = min(fit.p_better, key=lambda k: fit.p_better[k])
        own = dict(zip(fit.cells, fit.grades, strict=True))

        paired_breakdown: dict[ArmKey, dict[str, float]] = {
            pid: {
                "p_better": float(p_better),
                # The graded cells the fit read, never `n`, which counts errored rows too.
                "n_paired": float(len(fit.priors[pid])),
                "n_discordant": float(
                    sum(
                        discordance(
                            [own[c] for c in fit.priors[pid]], list(fit.priors[pid].values())
                        )
                    )
                ),
            }
            for pid, p_better in fit.p_better.items()
        }

        if self._on_snapshot is not None and self._current is not None:
            self._on_snapshot(
                RaceSnapshot(
                    p_best=float(fit.p_best),
                    current=self._current,
                    n_samples=n,
                    paired_breakdown=paired_breakdown,
                )
            )

        if not self.early_stop:
            return None
        context: EliminationContext = {
            "p_best": float(fit.p_best),
            "leader": leader,
            "queries_scored": n,
            "total_queries": self.n_samples,
            "n_priors": len(fit.priors),
        }
        # Past the selector's own bar on every prior, the round's parent among them, it is decided.
        if self.admission is not None and fit.p_best >= self.admission:
            return PoBBStop(
                self.name,
                "locked_in",
                {**context, "gate": EliminationGate.LOCK_IN, "admission": self.admission},
                fit=fit,
                paired_breakdown=paired_breakdown,
            )
        # ε is the ONLY futility gate; the priors include the round's parent (`potter/race.py`).
        bar = self.epsilon_at(read)
        if fit.p_best >= bar:
            return None
        return PoBBStop(
            self.name,
            "eliminated",
            {**context, "gate": EliminationGate.EPSILON, "epsilon": float(bar)},
            fit=fit,
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
        if not self.early_stop:
            return None
        measured = [cell for cell in results if cell.scored]
        grades = paired_grades(measured)
        cells = list(grades)
        # A prior's absent cells are read worst-case by the bounds, as `check` reads it on shared ones.
        priors = dict(self.priors_by_sample)
        pending = {
            pid: paired_grades(rows)
            for pid, rows in (unresolved or {}).items()
            if pid not in self.priors_by_sample
        }
        priors.update(pending)
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
            settled = [
                pid
                for pid, g in priors.items()
                if pid not in pending and all(s in g for s in cells)
            ]
            low, high = elimination_p_best_bounds(
                cells, grades, priors, self.ruler, settled=settled
            )
            locks = self.admission is not None and high >= self.admission
            if locks or low < self.epsilon_at(read):
                return m
        return None
