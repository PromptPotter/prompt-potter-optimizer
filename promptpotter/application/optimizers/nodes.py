"""What the bench asks of an optimizer's node implementations: the one Protocol per node type it
walks a manifest through (`docs/developer/node-standard.md` § Optimizer node types)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from promptpotter.application.campaign_config import Estimand
from promptpotter.application.scoring.query_loop import CatchUps
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.strict_model import StrictModel

if TYPE_CHECKING:
    import asyncio

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.optimizer_state import OptimizerState
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results import (
        CandidateProposal,
        ReferenceReading,
        RoundResult,
        ScoredCandidate,
    )
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule, StopSignal

    # A catch-up the bench measures for an eliminator: a prior's configuration on one cell, the
    # call already started and the commit that writes its row once a walk takes the cell.
    CatchUp = tuple[asyncio.Future[Any], Callable[[], list[QueryMeasurement]]]
    CatchUpFn = Callable[[JobSearchPoint, Sample, str], CatchUp]

__all__ = [
    "Adapter",
    "Boundary",
    "Controller",
    "Eliminator",
    "Measured",
    "MemberCoupling",
    "NodeMember",
    "Panel",
    "Population",
    "Proposer",
    "Race",
    "RoundContext",
    "Sampler",
    "Selection",
    "Selector",
]


@dataclass(frozen=True)
class MemberCoupling:
    """A relationship between a member's knobs (and, through ``bench_knobs``, the campaign's own)
    that the config map reports. ``predicate(config, knobs, declared)`` is True in the violating
    combination; ``declared`` is the knobs as the manifest file states them, before any overlay."""

    name: str
    knobs: tuple[str, ...]
    bench_knobs: tuple[str, ...]
    estimand: Estimand
    relation: str
    consequence: str
    severity: str
    predicate: Callable[[CampaignConfig, Any, Any], bool]


class NodeMember(Protocol):
    """``name`` is the manifest node it answers and ``knobs`` validates that node's ``config``:
    every field required, so the manifest is the one place a value comes from."""

    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> NodeKind: ...
    @property
    def knobs(self) -> type[StrictModel]: ...
    @property
    def couplings(self) -> tuple[MemberCoupling, ...]: ...


@dataclass(frozen=True)
class RoundContext:
    """One round as every node of its walk sees it."""

    cycle: Cycle
    round_num: int
    callbacks: RunCallbacks
    is_final_round: bool = False


@dataclass(frozen=True)
class Panel:
    """The round's cells, and the order every arm walks them in. The parent's re-score reads
    ``cells``; the walks read ``order``."""

    cells: list[Sample]
    order: list[Sample]


@dataclass(frozen=True)
class Population:
    """The round's individuals with the configuration each is measured under, one per proposal,
    and the optimizer state proposing them left behind — what the round document banks."""

    proposals: list[CandidateProposal]
    individuals: list[OptSearchPoint]
    pipeline_params: list[dict[str, Any] | None]
    optimizer_state: OptimizerState


@dataclass(frozen=True)
class Measured:
    """The measurement's output. ``scores`` carry each arm's reading against the round's reference
    (``parent``); ``electable`` is who the round can read, coverage floor applied."""

    rows: dict[str, list[QueryMeasurement]]
    scores: list[ScoredCandidate]
    scored: list[OptSearchPoint]
    parent: ReferenceReading
    parent_rows: list[QueryMeasurement]
    electable: list[str]
    coverage_floor: int


@dataclass(frozen=True)
class Selection:
    """What the selector keeps. ``selected_id`` is empty when the round holds its parent."""

    selected_id: str
    scores: list[ScoredCandidate]
    verdict_reason: str
    optimizer_state: OptimizerState


@dataclass(frozen=True)
class Boundary:
    """The controller's reading of a closed round: a stop, or whether it acts at the boundary."""

    stop: StopReason | None
    act: bool


class Sampler(NodeMember, Protocol):
    def draw(self, ctx: RoundContext, pool: list[Sample]) -> Panel: ...


class Proposer(NodeMember, Protocol):
    """An ``llm`` or ``algorithm`` node before the measurement. The first proposer of a walk is
    handed no population; each later one takes the one before it."""

    async def propose(
        self, ctx: RoundContext, panel: Panel, population: Population | None
    ) -> Population: ...


class Race(CatchUps, Protocol):
    """One round's elimination, as the measurement drives it. The catch-ups are the calls pairing
    a prior with the arm on turn; ``judge`` reads a decided arm before ``admit`` makes it a prior,
    returning the eliminator's own reading of an arm it stopped."""

    @property
    def n_priors(self) -> int: ...

    def rule(self, ahead: Sequence[tuple[str, Walk | None]]) -> StopRule: ...

    def open_turn(self, candidate_id: str, idx: int, n: int) -> None: ...

    def judge(
        self,
        signal: StopSignal | None,
        *,
        candidate_id: str,
        results: list[QueryMeasurement],
        labels: dict[str, str],
    ) -> Mapping[str, Any] | None: ...

    def admit(
        self, candidate_id: str, results: list[QueryMeasurement], sp: JobSearchPoint
    ) -> None: ...


class Eliminator(NodeMember, Protocol):
    def race(self, ctx: RoundContext, panel: Panel, catch_up: CatchUpFn) -> Race: ...


class Selector(NodeMember, Protocol):
    def select(
        self, ctx: RoundContext, measured: Measured, population: Population
    ) -> Selection: ...


class Adapter(NodeMember, Protocol):
    """An ``llm`` node after the selector: it reads the closed round and writes into its
    ``optimizer_state`` for the next one."""

    async def adapt(self, ctx: RoundContext, round_result: RoundResult) -> None: ...


class Controller(NodeMember, Protocol):
    def stops_after(self, ctx: RoundContext, round_result: RoundResult) -> bool:
        """Whether this round ends the run on the controller's own account — asked before the
        boundary, so the walk spends no adapter on a round nothing follows."""
        ...

    def observe(self, ctx: RoundContext, round_result: RoundResult) -> Boundary: ...

    async def act(self, ctx: RoundContext) -> None: ...

    def standing(self, cycle: Cycle) -> tuple[int, int | None]:
        """The two numbers the round readout carries beside the round: rounds without advance, and
        the run's remaining allowance where the controller keeps one."""
        ...

    async def diagnose(self, ctx: RoundContext) -> None:
        """``--diag``: act on the round just closed, then show the next round's proposals
        without measuring them."""
        ...
