from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

from promptpotter.application.intelligence.rasch import (
    Observation,
    RaschPosterior,
    fit_rasch,
    responses_of,
    theta_cells,
    theta_on_ruler,
)
from promptpotter.application.optimizers.nodes import RaceSnapshot
from promptpotter.application.optimizers.potter.knobs import PoBBKnobs
from promptpotter.domain.cycle_paths import Lens
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.ruler import DeltaRuler
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.worded import Worded

# θ draws per field member, enough that a p_best near ε reads the same across seeds.
_DRAWS = 4000


# The one stop PoBB files, so the one its abort lens switches off: measurement stopped, NOT a verdict.
_GATE = "epsilon"

ABORT_LENS_SUPPRESS: dict[str, frozenset[str]] = {"all_off": frozenset({_GATE})}

# The browser's picklist, keyed as the `lens=` it sends; exactly what `mask/tree_lens.py` accepts.
ABORT_LENS_LABELS: Worded[str, str] = Worded(
    {
        Lens("abort", "all_off").spelling: "No early abort",
    }
)
assert set(ABORT_LENS_LABELS) == {Lens("abort", body).spelling for body in ABORT_LENS_SUPPRESS}, (
    "abort-lens vocabulary drift between ABORT_LENS_LABELS and ABORT_LENS_SUPPRESS"
)


class EliminationContext(TypedDict, total=False):
    """``epsilon`` is the ε(n) that fired."""

    gate: str
    p_best: float
    epsilon: float
    leader: ArmKey
    queries_scored: int
    total_queries: int
    n_priors: int


@dataclass(frozen=True)
class PoBBStop(StopSignal):
    """What a cut read: ``cells``, each rival's rows and the arm's own on the cells the field was
    read on, by key, the arm last as its field was fit; `race.py::EliminationCut` records them."""

    check_result: EliminationContext
    cells: dict[ArmKey, list[str]]
    paired_breakdown: dict[ArmKey, float]


@dataclass(frozen=True)
class FieldRead:
    """PoBB's last field fit: each arm's θ on the ruler's scale, its posterior variance, its
    P(best) and the ε(n) its depth stood at; what ``per_decision`` and ``per_round`` choose on."""

    theta: dict[ArmKey, float]
    variance: dict[ArmKey, float]
    p_best: dict[ArmKey, float]
    bar: dict[ArmKey, float]


def race_sheet(cells: Iterable[GradedCell]) -> CellSheet:
    """An arm's rows as the race holds them; never filed, so it carries no scorer and no declaration."""
    return CellSheet("", frozenset(), tuple(cells))


def field_p_best(
    field: Mapping[ArmKey, CellSheet], cells: Collection[str]
) -> tuple[dict[ArmKey, float], dict[ArmKey, NDArray[np.float64]], RaschPosterior]:
    """Each member's posterior probability of being the field's best on *cells*, each member read
    on those it answered: one hierarchical Rasch fit of the field, θ and δ together, so a cell a
    member never answered is no hole, and a Monte Carlo over its θ draws, seeded by the read so a
    replay draws the same. The shares sum to 1; the fit rides beside them."""
    fit = fit_rasch(
        [
            Observation(key, cell, y)
            for key in field
            for cell, y in responses_of(field[key]).items()
            if cell in cells
        ]
    )
    if not fit.theta:
        return {}, {}, fit
    rng = np.random.default_rng(len(cells))
    draws = {
        ArmKey(key): rng.normal(fit.theta[key], fit.theta_se[key], _DRAWS) for key in fit.theta
    }
    best = np.argmax(np.vstack(list(draws.values())), axis=0)
    shares = np.bincount(best, minlength=len(draws)) / _DRAWS
    return dict(zip(draws, map(float, shares), strict=True)), draws, fit


class PoBBCheck:
    """A family-wise p_best on PoBB's joint posterior; ``docs/methods/candidate-elimination.md``."""

    name = "elimination"

    def __init__(
        self,
        knobs: PoBBKnobs,
        *,
        n_min: int,
        ruler: DeltaRuler | None,
        parent: ArmKey,
        on_snapshot: Callable[[RaceSnapshot], None] | None = None,
    ) -> None:
        # The SAME δ scale the round-winner election reads; ``None`` ⇒ flat, the ruler still cold.
        self.ruler = ruler
        self.parent = parent
        self.n_min = n_min
        # `per_round` hands each arm rows of its own, so the field is read on their union.
        self.union = knobs.shape == "per_round"
        self.epsilon = knobs.epsilon
        self.epsilon_floor = knobs.epsilon_floor
        self.epsilon_stop = knobs.epsilon_stop
        # The GRADED rows, never a binarized hit, degenerate on a graded backend.
        self.priors: dict[ArmKey, CellSheet] = {}
        self.read: FieldRead | None = None
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
        self, field: Mapping[ArmKey, CellSheet], standing: Mapping[ArmKey, int]
    ) -> dict[ArmKey, PoBBStop]:
        """An arm is cut when its posterior P(best of parent and every arm standing) falls below
        ε(n), n its own depth on the cells the field is read on: under `per_round` the union of the
        rows the racers answered, else those every racer shares. *standing* holds each arm's
        budget: its rows, a top-up's the prior's it extends."""
        racing = [key for key in standing if key != self.parent]
        if not racing:
            return {}
        answered = {key: [c.key for c in field[key].scoreable] for key in racing}
        if self.union:
            read = dict.fromkeys(cell for key in racing for cell in answered[key])
        else:
            shared = set.intersection(*(set(answered[key]) for key in racing))
            read = dict.fromkeys(cell for cell in answered[racing[0]] if cell in shared)
        parent = [c.key for c in field.get(self.parent, NO_CELLS).scoreable]
        # Every posterior gate and the ε ramp read the depth the election's coverage floor counts.
        cells = list(theta_cells(read, parent, self.ruler))
        held = set(cells)
        depth = {key: len(held.intersection(answered[key])) for key in racing}
        if max(depth.values()) < self.n_min:
            return {}
        p_best, draws, fit = field_p_best(field, cells)
        bar = {key: self.epsilon_at(n) for key, n in depth.items()}
        self.read = FieldRead(
            theta={ArmKey(key): theta for key, theta in theta_on_ruler(fit, self.ruler).items()},
            variance={ArmKey(key): se * se for key, se in fit.theta_se.items()},
            p_best=p_best,
            bar=bar,
        )
        stops: dict[ArmKey, PoBBStop] = {}
        for key in (key for key in racing if key in draws):
            rivals = {pid: field[pid] for pid in draws if pid != key}
            paired_breakdown = {pid: float(np.mean(draws[key] > draws[pid])) for pid in rivals}

            n = len(field[key].cells)
            if self._on_snapshot is not None:
                self._on_snapshot(RaceSnapshot(p_best[key], key, n, paired_breakdown))
            if not self.epsilon_stop or depth[key] < self.n_min or p_best[key] >= bar[key]:
                continue
            stops[key] = PoBBStop(
                self.name,
                "eliminated",
                {
                    "p_best": p_best[key],
                    "leader": max(rivals, key=p_best.__getitem__),
                    "queries_scored": n,
                    "total_queries": standing[key],
                    "n_priors": len(rivals),
                    "gate": _GATE,
                    "epsilon": float(bar[key]),
                },
                # The arm last, as its field was fit: the draws are seeded in that order.
                cells={
                    **{
                        pid: [c.key for c in rival.scoreable if c.key in held]
                        for pid, rival in rivals.items()
                    },
                    key: [cell for cell in cells if cell in set(answered[key])],
                },
                paired_breakdown=paired_breakdown,
            )
        return stops
