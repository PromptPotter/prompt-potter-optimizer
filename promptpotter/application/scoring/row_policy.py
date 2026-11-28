"""Which rows a race's survivors answer next, and how many calls each is sent: ONE policy any
eliminator's `Decider.decide` hands its view to, off its knobs (`RowKnobs`) and the field its cut
last fit (`FieldRead`), never a race's own copy (`candidate-elimination.md` § Who answers a
block's calls)."""

from __future__ import annotations

import math
import sys
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Annotated, Literal, NamedTuple

from pydantic import Field

from promptpotter.application.intelligence.rasch import expected_information, most_informative
from promptpotter.application.scoring.race_protocol import (
    Decision,
    Dispatch,
    ReadOrder,
    Trigger,
    View,
    WalkView,
    survivors,
)
from promptpotter.domain.campaign_config import Estimand, Knob
from promptpotter.domain.phase_acts import RowsRan
from promptpotter.domain.ruler import DeltaRuler, RulerEntry
from promptpotter.domain.sample import Sample
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import StopSignal

RowChoice = Literal["random", "informative"]
Refit = Literal["per_round", "per_decision"]
RowShape = Literal["per_candidate", "per_round"]


class RowKnobs(StrictModel):
    """The row policy's knobs, a group an eliminator's knobs include, so each keeps its path on
    that node's config. No knob carries a default: the manifest is the one place a number comes from."""

    row_choice: Annotated[RowChoice, Knob(Estimand.SELECTION)] = Field(
        description=(
            "Which rows each surviving candidate answers next, chosen at every block's open. "
            "``random``: the sampler's draw in its round order. ``informative``: the rows of the "
            "sampler's pool an answer tells θ most about (Fisher information at the θ ``refit`` "
            "seats, off the δ ruler, never off the parent's own grade on the row), so a round reads "
            "rows outside its draw. It reverses the round order's stratified interleave, so it runs "
            "under ``paired_election.vote: overlap`` alone; any other selector is refused."
        ),
    )
    refit: Annotated[Refit, Knob(Estimand.SELECTION)] = Field(
        description=(
            "The θ ``informative`` chooses rows on. ``per_round``: the parent's, on the ruler "
            "seated at the round's start. ``per_decision``: each survivor's in the field fit "
            "PoBB's last check made, moved onto that ruler's scale; the round's until a check "
            "first fits. Read under ``informative`` alone."
        ),
    )
    shape: Annotated[RowShape, Knob(Estimand.SELECTION)] = Field(
        description=(
            "Who answers a block's calls. ``per_candidate``: every survivor answers its next "
            "``block_size`` rows, the same rows under ``random``. ``per_round``: the block's calls "
            "(``block_size`` per survivor) go to the (candidate, row) pairs that tell PoBB's cut "
            "most, so each candidate answers rows of its own, none, one or several a block; the "
            "cut reads the field on the union of rows (``methods/candidate-elimination.md``)."
        ),
    )
    dispatch: Annotated[Dispatch, Knob(Estimand.SELECTION)] = Field(
        description=(
            "When the race decides. ``batch``: once every call a block handed out is back. "
            "``rolling``: on every returned row, each decision sending as many new calls as the "
            "information then warrants, none where the calls out can settle the cut, never "
            "waiting for the rest; the in-flight press caps what is out at once."
        ),
    )
    read_order: Annotated[ReadOrder, Knob(Estimand.SELECTION)] = Field(
        description=(
            "The order a ``rolling`` decision reads returns in. ``launch``: the order they were "
            "sent, so a press moves the bill, never the record. ``arrival``: as they come, the "
            "record then hanging on timing, so a resume replays the order the run read. Read "
            "under ``rolling`` alone."
        ),
    )

    @property
    def trigger(self) -> Trigger:
        return Trigger(self.dispatch == "rolling", self.read_order)

    def rows_ran(self, ruler: DeltaRuler | None) -> RowsRan:
        """A cold ruler tells every row alike, so ``informative`` there runs the sampler's order."""
        if self.row_choice == "random":
            return "random"
        return "cold" if ruler is None else "informative"


@dataclass(frozen=True)
class FieldRead[K: Hashable]:
    """A cut's last field fit: each arm's θ on the ruler's scale, its posterior variance, its
    P(best) and the ε(n) its depth stood at; what ``per_decision`` and ``per_round`` choose on."""

    theta: dict[K, float]
    variance: dict[K, float]
    p_best: dict[K, float]
    bar: dict[K, float]


@dataclass(frozen=True)
class RowGround[K: Hashable]:
    """What the policy reads beside the view: the round's ruler and θ, the depth the cut first
    reads (``None`` where the cut is off, so nothing settles), the sampler's block, and the rows
    each prior's walk stands on before its own (a top-up's)."""

    ruler: DeltaRuler | None
    theta: float
    n_min: int | None
    block_size: int
    priors: Mapping[K, int]


def send_rows[K: Hashable](
    view: View[K],
    stops: Mapping[K, StopSignal],
    read: FieldRead[K] | None,
    knobs: RowKnobs,
    ground: RowGround[K],
) -> Decision[K]:
    """Each survivor's sends and its rows' order. ``per_candidate``: every draw walk a block's
    rows, ``informative`` the most informative at one set of θ, so the survivors answer the same
    next rows. ``per_round``: each its own rows at its own θ, and the block's calls to the walks
    whose answers tell the cut most (`_hand_out`). A prior's walk is topped up on its own gaps, a
    block's rows in their order. Under ``rolling`` each walk is sent the calls its decision
    warrants, its calls out keeping their seats."""
    live = survivors(view, stops)

    def theta(key: K) -> float:
        if knobs.refit == "per_decision" and read is not None and key in read.theta:
            return read.theta[key]
        return ground.theta

    drawn = [key for key in live if key not in ground.priors]
    entries = None if ground.ruler is None else ground.ruler.entries()
    rows: dict[K, Sequence[Sample]] = {key: walk.untaken for key, walk in live.items()}
    if knobs.row_choice == "informative":
        shared = (
            [read.theta[key] for key in drawn if key in read.theta]
            if knobs.refit == "per_decision" and read is not None
            else []
        ) or [ground.theta]
        for key in drawn:
            at = [theta(key)] if knobs.shape == "per_round" else shared
            out = live[key].out
            rows[key] = [*rows[key][:out], *most_informative(rows[key][out:], at, ground.ruler)]

    def hand_out(walks: Mapping[K, WalkView], calls: int | None) -> dict[K, int]:
        return _hand_out(
            {
                key: _Walking(
                    theta(key),
                    rows[key][: walk.left],
                    walk.out,
                    ground.priors.get(key, 0) + len(walk.taken),
                )
                for key, walk in walks.items()
            },
            read,
            calls,
            entries,
            sys.maxsize if ground.n_min is None else ground.n_min,
        )

    calls: dict[K, int] = {}
    if knobs.dispatch == "rolling":
        calls = hand_out(live, None)
    elif knobs.shape == "per_round" and drawn:
        calls = hand_out({key: live[key] for key in drawn}, ground.block_size * len(drawn))
    sends = {
        key: rows[key][walk.out : min(calls.get(key, ground.block_size), walk.left)]
        for key, walk in live.items()
    }
    return Decision(stops, sends, rows)


class _Walking(NamedTuple):
    """A live walk as a decision hands it calls: the θ its rows are read at, the rows its budget
    still takes in the order it takes them, the first ``owed`` of them out, and its depth."""

    theta: float
    rows: Sequence[Sample]
    owed: int
    depth: int


def _hand_out[K: Hashable](
    walks: Mapping[K, _Walking],
    read: FieldRead[K] | None,
    calls: int | None,
    ruler: Mapping[str, RulerEntry] | None,
    n_min: int,
) -> dict[K, int]:
    """How many calls each walk answers, the calls it has out among them, off the rows it would
    take next at its θ. Each goes to the walk whose next answer tells the cut most: the Fisher
    information it carries about its θ, times the posterior variance it then removes
    (``variance²``), times how near the walk's P(best) sits to its ε(n) on the probit scale
    (`candidate-elimination.md` § Who answers a block's calls). A walk's variance shrinks with
    each call out or handed it; a tie goes to the walk handed fewer, then to walk order. Before
    the cut first fits, every walk's variance and nearness are 1: the information alone. *calls*
    ``None`` is a ``rolling`` decision's: it hands while a walk's calls out cannot settle the cut
    on it (`_settles`), none where they all can, and each walk at most its budget."""

    def info(key: K, at: int) -> float:
        walk = walks[key]
        return expected_information(walk.theta, (walk.rows[at].key,), ruler)

    handed = {key: walk.owed for key, walk in walks.items()}
    informed = {key: sum(info(key, at) for at in range(walk.owed)) for key, walk in walks.items()}
    variance = {
        key: read.variance[key] if read is not None and key in read.variance else 1.0
        for key in walks
    }
    for key, told in informed.items():
        if told:
            variance[key] = 1.0 / (1.0 / variance[key] + told)
    near = {key: _nearness(read, key) for key in walks}
    while calls is None or sum(handed.values()) < calls:
        best: tuple[float, int, K, float] | None = None
        for key, walk in walks.items():
            if handed[key] >= len(walk.rows):
                continue
            if calls is None and _settles(read, key, walk.depth, handed[key], informed[key], n_min):
                continue
            gain = info(key, handed[key])
            rank = (gain * variance[key] ** 2 * near[key], -handed[key])
            if best is None or rank > best[:2]:
                best = (*rank, key, gain)
        if best is None:
            break
        *_, key, gain = best
        handed[key] += 1
        informed[key] += gain
        variance[key] = 1.0 / (1.0 / variance[key] + gain)
    return handed


def _settles[K: Hashable](
    read: FieldRead[K] | None, key: K, depth: int, out: int, informed: float, n_min: int
) -> bool:
    """Whether the *out* calls a walk has out can settle the cut's decision on it, so a decision
    sends it none: they reach the depth the cut first reads it at, and its probit distance z from
    the bar lies within one preposterior sd of where their information *informed* moves it,
    z ≤ √(v·I / (1 + v·I)) (`candidate-elimination.md` § How many calls a rolling decision sends)."""
    if out == 0 or depth + out < n_min:
        return False
    if read is not None and key in read.p_best and key not in read.bar:
        # The parent stands no bar: the cut never decides it, so its calls settle nothing.
        return False
    if read is None or key not in read.p_best:
        return True
    reach = read.variance[key] * informed
    return _gap(read, key) <= math.sqrt(reach / (1.0 + reach))


_PROBIT = NormalDist()


def _gap[K: Hashable](read: FieldRead[K], key: K) -> float:
    """The probit distance z from a walk's P(best) down to its ε(n)."""
    clip = 1e-4
    p = min(max(read.p_best[key], clip), 1.0 - clip)
    return _PROBIT.inv_cdf(p) - _PROBIT.inv_cdf(read.bar[key])


def _nearness[K: Hashable](read: FieldRead[K] | None, key: K) -> float:
    """The normal density at the probit gap between a walk's P(best) and its ε(n), over the
    density at no gap: 1 on the bar, and falling as the cut's decision on it settles."""
    if read is None or key not in read.bar or key not in read.p_best:
        return 1.0
    return _PROBIT.pdf(_gap(read, key)) / _PROBIT.pdf(0.0)
