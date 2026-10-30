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
    curtailed_p_best,
    curtailed_support,
    discordance,
    paired_grades,
    paired_p_best,
    worst_case_p_best,
)
from promptpotter.domain.scoring import graded_answer, is_answer_collapsed
from promptpotter.domain.validators import StopSignal

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell


class EliminationGate(StrEnum):
    """Named by the producer: re-derived downstream from whichever keys survived, a collapse cut reads as an ε cut."""

    EPSILON = "epsilon"  # measurement stopped, NOT a verdict
    LOCK_IN = "lock_in"
    COLLAPSED = "collapsed"  # the ABSENCE of a measurement


ABORT_LENS_LABELS: dict[str, str] = {
    f"{EliminationGate.EPSILON.value}_off": "No ε-elimination",
    f"{EliminationGate.LOCK_IN.value}_off": "No lock-in",
    f"{EliminationGate.COLLAPSED.value}_off": "No collapse cut",
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
    """``gate`` says which keys EXIST: a collapse carries the two depths alone; lock-in and ε add the posterior, its bounding prior and the ε(n) that fired."""

    gate: EliminationGate
    p_best: float
    epsilon: float
    leader: ArmKey
    queries_scored: int
    total_queries: int
    n_priors: int


@dataclass(frozen=True)
class PoBBStop(StopSignal):
    """``fit`` is the pairing a replay re-fits, and ``None`` on a collapse, which read none;
    ``read`` each prior's grades the gate tested: its shared cells, or on a lock-in the panel."""

    check_result: EliminationContext
    fit: PairedPosterior | None
    paired_breakdown: dict[ArmKey, dict[str, float]]
    read: dict[ArmKey, dict[str, PairedGrade]]


class PoBBCheck:
    """Each prior is read on the candidate's exact cells; ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        knobs: PoBBKnobs,
        *,
        n_min: int,
        panel: Sequence[str],
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
        # The cells every walk is asked, in its order: a lock-in reads each one left at its worst.
        self.panel = list(panel)
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
        # Cut HERE, not by the posterior: one label for everything can score above the ε floor.
        if is_answer_collapsed(results):
            return PoBBStop(
                self.name,
                "eliminated",
                {
                    "gate": EliminationGate.COLLAPSED,
                    "queries_scored": n,
                    "total_queries": len(self.panel),
                },
                fit=None,
                paired_breakdown={},
                read={},
            )
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

        # ε is the ONLY futility gate. Its mirror locks in by curtailment alone: past the bar the
        # election admits on (strictly over 1 − ε) with every cell left lost, no cell can undo it.
        bar = self.epsilon_at(read)
        if not self.epsilon_stop:
            return None
        if fit.p_best < bar:
            gate, p_best, tested = EliminationGate.EPSILON, fit.p_best, fit.priors
        else:
            tested = {
                pid: {c: g for c, g in grades.items() if c in self.panel}
                for pid, grades in self.priors_by_sample.items()
            }
            p_best = curtailed_p_best(self.panel, own, tested, self.ruler)
            if p_best <= 1.0 - bar:
                return None
            gate = EliminationGate.LOCK_IN
        context: EliminationContext = {
            "gate": gate,
            "p_best": float(p_best),
            "epsilon": float(bar),
            "leader": leader,
            "queries_scored": n,
            "total_queries": len(self.panel),
            "n_priors": len(fit.priors),
        }
        return PoBBStop(
            self.name,
            "locked_in" if gate is EliminationGate.LOCK_IN else "eliminated",
            context,
            fit=fit,
            paired_breakdown=paired_breakdown,
            read=tested,
        )

    def earliest_stop(
        self,
        results: Sequence[GradedCell],
        upcoming: Sequence[tuple[Sample, GradedCell | None]],
        *,
        unresolved: Mapping[ArmKey, Iterable[GradedCell]] | None = None,
    ) -> int | None:
        """An ``unresolved`` candidate may never become a prior: graded where its rows say, anything elsewhere, never counted on."""
        measured = [cell for cell in results if cell.scored]
        grades = paired_grades(measured)
        cells = list(grades)
        said = {graded_answer(cell.facts, cell.grade) for cell in measured}
        # A prior's absent cells are read worst-case, as `check` reads it on shared ones.
        priors = {
            **{pid: paired_grades(rows) for pid, rows in (unresolved or {}).items()},
            **self.priors_by_sample,
        }
        for m, (sample, row) in enumerate(upcoming, start=len(results) + 1):
            if row is None or row.scored:
                cells.append(sample.key)
            if row is not None and row.scored:
                grades.update(paired_grades([row]))
                said.add(graded_answer(row.facts, row.grade))
            if m < self.n_min:
                continue
            # Collapse needs one answer everywhere; a second, or no answer, rules it out for good.
            if len(said) <= 1 and None not in said:
                return m
            # Cold, a catch-up may yet give the parent any cell, and a depth below what `check` will
            # read lands the horizon past its stop: so it counts the most it could read.
            read = len(theta_cells(dict.fromkeys(cells), cells, self.ruler))
            if not self.epsilon_stop or not priors or read < self.n_min:
                continue
            bar = self.epsilon_at(read)
            if worst_case_p_best(cells, grades, priors, self.ruler) < bar:
                return m
            # On the priors already standing: one that joins can only lower a lock-in's reading.
            if (
                curtailed_support(self.panel, grades, self.priors_by_sample, reached=cells)
                > 1 - bar
            ):
                return m
        return None
