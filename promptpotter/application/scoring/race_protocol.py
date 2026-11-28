"""What a phase of walks asks its race (`query_loop.py::run_walks`), and when: ONE call,
`Decider.decide`, on a view of every live walk, answered by the stops it cuts and the calls it
sends. The phase keeps one queue of the calls sent; the trigger names when it asks."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from promptpotter.domain.arms import Elimination
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_acts import RowsRan
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, GradedCell
from promptpotter.domain.validators import StopSignal, WalkStop

# When a race decides: once no call it sent is out, or on every returned row.
Dispatch = Literal["batch", "rolling"]
# The order a `rolling` phase reads returns in: the order they were sent, or as they come.
ReadOrder = Literal["launch", "arrival"]


@dataclass(frozen=True)
class Trigger:
    """When a phase asks its race, and the order it reads the calls sent: ``batch`` decides once no
    call is out, each decision closing a block every survivor shares; ``rolling`` on each read."""

    each_read: bool
    reads: ReadOrder = "launch"

    def ahead(self, cells: int, past: int) -> int:
        """The calls a press of *cells* may send past a walk's decided ones, *past* of them out:
        where no read decides before those are read, a cut there wastes the press at most; where
        each read decides, a call past them would be no decision's."""
        return 0 if self.each_read else max(cells - past, 0)

    def sure(self, taken: int, decided: int) -> int:
        """The rows a walk is sure to take, *decided* past its *taken* ones sent: none past those
        taken where the next read decides."""
        return taken if self.each_read else taken + decided


BATCH = Trigger(each_read=False)


@dataclass(frozen=True)
class WalkView:
    """A live walk as its race decides on it."""

    taken: Sequence[GradedCell]
    # Its untaken rows in the order it takes them, the first ``out`` of them sent and not yet read.
    untaken: Sequence[Sample]
    out: int
    # The rows it takes in all.
    budget: int

    @property
    def left(self) -> int:
        return self.budget - len(self.taken)


@dataclass(frozen=True)
class View[K: Hashable]:
    walks: Mapping[K, WalkView]
    # The phase's first decision: no row read yet, so it cuts none and only sends.
    opening: bool


@dataclass(frozen=True)
class Decision[K: Hashable]:
    """``order``: a walk whose untaken rows the race ranks, all of them, those out leading, then its
    ``sends``; a walk it names none keeps its own, its sends leading the rest."""

    stops: Mapping[K, StopSignal] = field(default_factory=dict)
    # Each survivor's new calls, past those it has out, in the order it takes them.
    sends: Mapping[K, Sequence[Sample]] = field(default_factory=dict)
    order: Mapping[K, Sequence[Sample]] = field(default_factory=dict)


class Decider[K: Hashable](Protocol):
    @property
    def trigger(self) -> Trigger: ...

    def decide(self, view: View[K]) -> Decision[K]:
        """Read off the rows taken and the calls decisions sent alone: no cell a press sent ahead
        enters, so a press never moves a decision."""
        ...


def survivors[K: Hashable](view: View[K], stops: Mapping[K, StopSignal]) -> dict[K, WalkView]:
    """The walks a decision sends to: neither cut nor done."""
    return {key: walk for key, walk in view.walks.items() if key not in stops and walk.left}


def next_block[K: Hashable](view: View[K], stops: Mapping[K, StopSignal], size: int) -> Decision[K]:
    """Each survivor its next *size* rows, its budget capping them: a block race on its sampler's
    order, which chooses no row."""
    return Decision(
        stops,
        {
            key: walk.untaken[walk.out : min(size, walk.left)]
            for key, walk in survivors(view, stops).items()
        },
    )


class Race(Decider[ArmKey], Protocol):
    """``judge`` reads a decided arm BEFORE ``admit`` makes it a prior; ``decide`` (`race_protocol.py`)
    cuts and sends to every live walk at once. A race whose arms all walk one panel keeps no
    prior, so it inherits ``priors``, ``admit`` and ``catch_up``."""

    @property
    def n_priors(self) -> int: ...

    @property
    def priors(self) -> Mapping[ArmKey, CellSheet]:
        """Each prior a later arm is paired with, in prior order: the bench catches it up on a cell it lacks."""
        return {}

    @property
    def top_up(self) -> Decider[ArmKey] | None:
        """The race walked on over the origin panel's top-up; ``None`` where it ends with the draw."""
        return None

    def judge(
        self, signal: StopSignal | None, key: ArmKey, labels: Mapping[ArmKey, str]
    ) -> Elimination | None: ...

    def admit(self, _key: ArmKey, _results: Sequence[GradedCell]) -> None:
        """A decided arm's rows: it joins the priors."""
        return None

    def catch_up(self, _key: ArmKey, _cells: Sequence[GradedCell]) -> None:
        """A prior's caught-up cells, folded onto the rows it holds."""
        return None

    @property
    def trigger(self) -> Trigger:
        """``batch``: a race decides once no call it sent is out."""
        return BATCH

    @property
    def rows_ran(self) -> RowsRan:
        """The row choice it runs, which the round's measure record names."""
        return "random"


class BlockRace(Race):
    """A block race on its sampler's order (`next_block`): it cuts once a block closes, never at
    the opening, and books every stop in ONE signal shape, which ``ruling`` words."""

    def __init__(self, node: str, *, block_size: int, n_cells: int) -> None:
        self.node, self.block_size, self.n_cells = node, block_size, n_cells
        # The walks the last decision left racing.
        self.racing: set[ArmKey] = set()

    def decide(self, view: View[ArmKey]) -> Decision[ArmKey]:
        stops = {} if view.opening else self.cut(view)
        self.racing = {key for key in view.walks if key not in stops}
        return next_block(view, stops, self.block_size)

    @abstractmethod
    def cut(self, view: View[ArmKey]) -> dict[ArmKey, StopSignal]: ...

    def signal(self, outcome: WalkStop, gate: str, taken: int, **context: Any) -> StopSignal:
        return StopSignal(
            self.node,
            outcome,
            {"gate": gate, "queries_scored": taken, "total_samples": self.n_cells, **context},
        )

    def judge(
        self, signal: StopSignal | None, key: ArmKey, labels: Mapping[ArmKey, str]
    ) -> Elimination | None:
        if signal is None or signal.check_name != self.node:
            return None
        cr = signal.check_result
        context = {
            "gate": cr["gate"],
            "queries_scored": cr["queries_scored"],
            "total_queries": cr["total_samples"],
        }
        return self.ruling(signal, context, key, labels)

    @abstractmethod
    def ruling(
        self,
        signal: StopSignal,
        context: dict[str, Any],
        key: ArmKey,
        labels: Mapping[ArmKey, str],
    ) -> Elimination:
        """*signal* worded on *context*, which holds what every block race's elimination names."""
        ...
