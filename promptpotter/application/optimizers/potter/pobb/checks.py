from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

from promptpotter.application.intelligence.exploration import graded_response
from promptpotter.application.optimizers.nodes import RaceSnapshot
from promptpotter.application.scoring.selection import (
    PairedPosterior,
    elimination_p_best_bounds,
    paired_p_best,
    priors_covering,
)
from promptpotter.domain.results import ArmOutcome
from promptpotter.domain.scoring import NO_RESULT, is_answer_collapsed
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.statistics import discordant_counts

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell


def _graded(cells: Iterable[GradedCell]) -> dict[int, float]:
    return {cell.ruler_key: graded_response(cell) for cell in cells if cell.scored}


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
    """``gate`` says which keys EXIST: a collapse carries the two depths alone, lock-in adds posterior and bounding prior, ε its bar."""

    gate: EliminationGate
    p_best: float
    epsilon: float
    leader_id: str
    queries_scored: int
    total_queries: int
    n_priors: int
    leader_label: str


@dataclass(frozen=True)
class PoBBStop(StopSignal):
    """``fit`` is the pairing a replay re-fits, and ``None`` on a collapse, which read none."""

    check_result: EliminationContext
    fit: PairedPosterior | None
    paired_breakdown: dict[str, dict[str, float]]


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
    ) -> None:
        # The SAME δ scale the round-winner election reads; ``None`` ⇒ flat, the ruler still cold.
        self.ruler = ruler
        self.n_min = n_min
        self.epsilon = knobs.epsilon
        self.epsilon_floor = knobs.epsilon_floor
        self.lock_in = knobs.lock_in
        self.lock_in_n_min = knobs.lock_in_n_min
        self.epsilon_elimination = knobs.epsilon_elimination
        self.leader_lock_in = knobs.leader_lock_in
        self.n_samples = n_samples
        # The GRADED response, never a binarized hit, which is degenerate on a graded backend.
        self.priors_by_sample: dict[str, dict[int, float]] = {}
        self._current_id: str = ""
        self._on_snapshot: Callable[[RaceSnapshot], None] | None = None

    @property
    def prior_ids(self) -> list[str]:
        return list(self.priors_by_sample)

    def set_current(
        self,
        candidate_id: str,
        on_snapshot: Callable[[RaceSnapshot], None] | None = None,
    ) -> None:
        self._current_id = candidate_id
        self._on_snapshot = on_snapshot

    def register_completed(self, results: Iterable[GradedCell], *, candidate_id: str) -> None:
        self.priors_by_sample[candidate_id] = _graded(results)

    def extend_prior(self, candidate_id: str, rows: Iterable[GradedCell]) -> None:
        self.priors_by_sample[candidate_id].update(_graded(rows))

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
                ArmOutcome.ELIMINATED,
                {
                    "gate": EliminationGate.COLLAPSED,
                    "queries_scored": n,
                    "total_queries": self.n_samples,
                },
                fit=None,
                paired_breakdown={},
            )
        fit = paired_p_best(results, self.priors_by_sample, self.ruler)
        if fit is None:
            return None
        leader_id = min(fit.p_better, key=lambda k: fit.p_better[k])

        paired_breakdown: dict[str, dict[str, float]] = {
            pid: {
                "p_better": float(p_better),
                # The graded cells the fit read, never `n`, which counts errored rows too.
                "n_paired": float(len(fit.cells)),
                "n_discordant": float(sum(discordant_counts(fit.grades, fit.priors[pid]))),
            }
            for pid, p_better in fit.p_better.items()
        }

        if self._on_snapshot is not None:
            self._on_snapshot(
                RaceSnapshot(
                    p_best=float(fit.p_best),
                    current_id=self._current_id or "__current__",
                    n_samples=n,
                    paired_breakdown=paired_breakdown,
                    decision_grade=n >= self.lock_in_n_min,
                )
            )

        context: EliminationContext = {
            "gate": EliminationGate.LOCK_IN,
            "p_best": float(fit.p_best),
            "leader_id": leader_id,
            "queries_scored": n,
            "total_queries": self.n_samples,
            "n_priors": len(fit.priors),
        }
        if self.leader_lock_in and n >= self.lock_in_n_min and fit.p_best >= self.lock_in:
            return PoBBStop(
                self.name, ArmOutcome.LOCKED_IN, context, fit=fit, paired_breakdown=paired_breakdown
            )

        # ε is the ONLY futility gate; the priors include the round's parent (`potter/race.py`).
        bar = self.epsilon_at(n)
        if not self.epsilon_elimination or fit.p_best >= bar:
            return None
        return PoBBStop(
            self.name,
            ArmOutcome.ELIMINATED,
            {**context, "gate": EliminationGate.EPSILON, "epsilon": float(bar)},
            fit=fit,
            paired_breakdown=paired_breakdown,
        )

    def earliest_stop(
        self,
        results: Sequence[GradedCell],
        upcoming: Sequence[tuple[Sample, GradedCell | None]],
        *,
        unresolved: Mapping[str, Iterable[GradedCell]] | None = None,
    ) -> int | None:
        """An ``unresolved`` candidate may never become a prior: graded where its rows say, anything elsewhere, never counted on."""
        measured = [cell for cell in results if cell.scored]
        grades = _graded(measured)
        cells = list(grades)
        said = {cell.facts.predicted for cell in measured}
        priors = priors_covering(self.priors_by_sample, cells)
        pending = {
            pid: _graded(rows)
            for pid, rows in (unresolved or {}).items()
            if pid not in self.priors_by_sample
        }
        priors.update(pending)
        for m, (sample, row) in enumerate(upcoming, start=len(results) + 1):
            if row is None or row.scored:
                cells.append(sample.id)
            if row is not None and row.scored:
                grades[sample.id] = graded_response(row)
                said.add(row.facts.predicted)
            if m < self.n_min:
                continue
            # Collapse needs one answer everywhere; a second, or an empty one, rules it out for good.
            if len(said) <= 1 and not said & {"", NO_RESULT}:
                return m
            if not priors:
                continue
            settled = [
                pid
                for pid, g in priors.items()
                if pid not in pending and all(s in g for s in cells)
            ]
            low, high = elimination_p_best_bounds(
                cells, grades, priors, self.ruler, settled=settled
            )
            if self.leader_lock_in and m >= self.lock_in_n_min and high >= self.lock_in:
                return m
            if self.epsilon_elimination and low < self.epsilon_at(m):
                return m
        return None


__all__ = [
    "ABORT_LENS_LABELS",
    "ABORT_LENS_SUPPRESS",
    "EliminationContext",
    "EliminationGate",
    "PoBBCheck",
    "PoBBStop",
]
