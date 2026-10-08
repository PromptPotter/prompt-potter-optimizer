"""Mid-round elimination — PoBBCheck (Russo 2016 stop rule)."""

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
from promptpotter.config.settings import NO_RESULT
from promptpotter.domain.results import ArmOutcome
from promptpotter.domain.scoring import is_answer_collapsed, is_graded
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.statistics import discordant_counts

if TYPE_CHECKING:
    from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
    from promptpotter.domain.ruler import DeltaRuler
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement


def _graded(rows: Iterable[QueryMeasurement]) -> dict[str, float]:
    """Each cell's grade; a row carrying no verdict carries no outcome for the θ fit."""
    return {
        str(sid): graded_response(r)
        for r in rows
        if (sid := r.get("sample_id")) is not None and is_graded(r)
    }


class EliminationGate(StrEnum):
    """WHICH gate stopped the candidate, named here because only the producer knows: re-derived
    downstream from whichever keys survived, a collapse cut reads as an ε cut. These are the
    mask's abort contributors."""

    EPSILON = "epsilon"  # posterior fell below ε — measurement stopped, NOT a verdict
    LOCK_IN = "lock_in"  # the opposite verdict: far enough ahead to stop buying
    COLLAPSED = "collapsed"  # one label for every sample — the ABSENCE of a measurement


# The operator's word for switching one gate off, keyed by the `abort:` lens variant
# (`ABORT_LENS_SUPPRESS` below, derived from the same enum).
ABORT_LENS_LABELS: dict[str, str] = {
    f"{EliminationGate.EPSILON.value}_off": "No ε-elimination",
    f"{EliminationGate.LOCK_IN.value}_off": "No lock-in",
    f"{EliminationGate.COLLAPSED.value}_off": "No collapse cut",
    "all_off": "No early abort",
}

# Abort-lens variants → the gate(s) each switches off (the mask's abort verdict; see
# docs/operations/mask-projection.md). DERIVED from `EliminationGate`, so a gate added there is
# switchable rather than silently unsuppressable.
ABORT_LENS_SUPPRESS: dict[str, frozenset[str]] = {
    **{f"{g.value}_off": frozenset({g.value}) for g in EliminationGate},
    "all_off": frozenset(g.value for g in EliminationGate),
}

# The picklist the browser offers must be exactly what `mask/tree_lens.py` accepts. A LABEL cannot be
# derived — it is copy — so the key set is asserted instead: the browser's options are emitted
# from `ABORT_LENS_LABELS` by `scripts/build_ts_types.py`, and a gate without a word for it would
# otherwise be served and unofferable.
assert set(ABORT_LENS_LABELS) == set(ABORT_LENS_SUPPRESS), (
    "abort-lens vocabulary drift: "
    f"unlabelled {sorted(set(ABORT_LENS_SUPPRESS) - set(ABORT_LENS_LABELS))}, "
    f"unserved {sorted(set(ABORT_LENS_LABELS) - set(ABORT_LENS_SUPPRESS))}"
)


class EliminationContext(TypedDict, total=False):
    """PoBB's ``ScoredCandidate.elimination_context`` for an arm it cut or locked. ``gate`` says
    which of the rest EXIST: a collapse carries the two depths alone, lock-in adds the posterior
    and the prior that bounds it, and ε its bar."""

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
    """``fit`` is the pairing the posterior was read on — what the decision archives and a replay
    re-fits — and ``None`` on a collapse, which read none."""

    check_result: EliminationContext
    fit: PairedPosterior | None
    paired_breakdown: dict[str, dict[str, float]]


class PoBBCheck:
    """Paired-sample PoBB stop rule over the priors it is handed, each read on the candidate's
    exact cells. ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        knobs: PoBBKnobs,
        *,
        n_min: int,
        n_samples: int,
        ruler: DeltaRuler | None,
    ) -> None:
        # The cycle's FIXED δ ruler — the SAME scale the round-winner election reads, so
        # elimination θ and election θ agree (``None`` ⇒ flat, where the ruler is still cold).
        self.ruler = ruler
        # The bench's `elimination_n_min`: the fewest cells an arm is judged on, at either end.
        self.n_min = n_min
        self.epsilon = knobs.epsilon
        self.epsilon_floor = knobs.epsilon_floor
        self.lock_in = knobs.lock_in
        self.lock_in_n_min = knobs.lock_in_n_min
        self.epsilon_elimination = knobs.epsilon_elimination
        self.leader_lock_in = knobs.leader_lock_in
        self.n_samples = n_samples
        # Each prior's GRADED response per cell, in the order the priors joined: on a graded
        # backend a binarized hit is degenerate, so the θ fit reads the grade itself.
        self.priors_by_sample: dict[str, dict[str, float]] = {}
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

    def register_completed(self, results: Iterable[QueryMeasurement], *, candidate_id: str) -> None:
        self.priors_by_sample[candidate_id] = _graded(results)

    def extend_prior(self, candidate_id: str, rows: Iterable[QueryMeasurement]) -> None:
        self.priors_by_sample[candidate_id].update(_graded(rows))

    def epsilon_at(self, n: int) -> float:
        """The ε bar at depth *n*: ``epsilon_floor`` at ``n_min``, ramping linearly up to
        ``epsilon`` over the next ``n_min`` cells and holding it to the end — flat wherever the
        floor is not below ``epsilon``, so a config that sets neither eliminates on one scalar."""
        if self.epsilon <= self.epsilon_floor:
            return self.epsilon
        scale = min(1.0, max(0.0, (n - self.n_min) / max(self.n_min, 1)))
        return self.epsilon_floor + (self.epsilon - self.epsilon_floor) * scale

    def check(self, results: list[QueryMeasurement]) -> StopSignal | None:
        n = len(results)
        if n < self.n_min:
            return None
        # A constant answerer is cut HERE, not at the election. ``is_answer_collapsed`` is the
        # absence of a measurement, not a low score, and the two are not interchangeable: an arm
        # answering one label to everything scores whatever share of the subset carries that
        # label — on a three-way task that can sit near 0.33, comfortably above the ε floor — so
        # the posterior never fires and the arm measures its full budget before ``l1_score``
        # drops it from ``electable`` anyway. Measured live 2026-07-28: an arm answering
        # "Uncertain" 12/12 against 6 TRUE / 6 FALSE spent twelve samples to establish something
        # the fourth had already shown. Asking the question at ``n_min`` (the same evidence floor
        # the posterior waits for — no second constant, and by then a genuine reasoner emitting
        # one label while truths vary is unlikely) turns it into what a human does: see a
        # candidate that has stopped answering the question, and move on.
        #
        # The collapse is still CHARGED, not hidden — the arm keeps its rows, so
        # the outer loop sees it structurally, via elimination rather than a graded charge.
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
        # The prior the minimum is read against — the same metric the round-winner election
        # ranks by, taken over every prior the arm is paired with.
        leader_id = min(fit.p_better, key=lambda k: fit.p_better[k])

        paired_breakdown: dict[str, dict[str, float]] = {
            pid: {
                "p_better": float(p_better),
                # The graded cells the fit read, never `n`: `n` counts errored rows too and rides
                # the snapshot as `n_samples`.
                "n_paired": float(len(fit.cells)),
                # Concordant cells cannot say which arm is better, so this is the width
                # `p_better` was entitled to, beside the width it was measured over.
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
        # Leader lock-in: stop measuring when P(cand > every prior) ≥ lock_in.
        if self.leader_lock_in and n >= self.lock_in_n_min and fit.p_best >= self.lock_in:
            return PoBBStop(
                self.name, ArmOutcome.LOCKED_IN, context, fit=fit, paired_breakdown=paired_breakdown
            )

        # ε is the ONLY futility gate and tests the bar adoption does: the priors include the
        # round's parent (`potter/race.py`), and crowning needs a strictly positive θ lift over it.
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
        results: list[QueryMeasurement],
        upcoming: Sequence[tuple[Sample, QueryMeasurement | None]],
        *,
        unresolved: Mapping[str, Iterable[QueryMeasurement]] | None = None,
    ) -> int | None:
        """Each gate of :meth:`check`, asked of every completion at once. Priors already short of a
        measured cell stay out, as they do there: the backfill that could cover it has run.
        ``unresolved`` are candidates ahead of this one still being walked, with the rows each has
        back so far. Each may yet become a prior or never become one, so it is graded where its
        rows say and anything anywhere else, and never counted on."""
        measured = [r for r in results if is_graded(r)]
        grades = {int(r.get("sample_id", 0)): graded_response(r) for r in measured}
        cells = list(grades)
        said = {str(r.get("predicted") or "") for r in measured}
        priors = {
            pid: {int(s): g for s, g in held.items()}
            for pid, held in priors_covering(self.priors_by_sample, [str(s) for s in cells]).items()
        }
        pending = {
            pid: {int(s): g for s, g in _graded(rows).items()}
            for pid, rows in (unresolved or {}).items()
            if pid not in self.priors_by_sample
        }
        priors.update(pending)
        for m, (sample, row) in enumerate(upcoming, start=len(results) + 1):
            if row is None or is_graded(row):
                cells.append(sample.id)
            if row is not None and is_graded(row):
                grades[sample.id] = graded_response(row)
                said.add(str(row.get("predicted") or ""))
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
