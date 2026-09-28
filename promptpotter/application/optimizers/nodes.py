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
    from types import ModuleType

    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.cycle import Cycle
    from promptpotter.application.optimization.resume_and_fork.decisions import GatingMode
    from promptpotter.application.optimization.resume_and_fork.replayers import Replayer
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.run_observers import RunCallbacks
    from promptpotter.application.scoring.query_loop import Walk
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.optimizer_state import OptimizerState
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results import (
        CandidateProposal,
        ReferenceReading,
        RoundResult,
        ScoredCandidate,
    )
    from promptpotter.domain.run_records import ResumeCheckpointKind
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import QueryMeasurement
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule, StopSignal
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

    # A catch-up the bench measures for an eliminator: a prior's configuration on one cell, the
    # call already started and the commit that writes its row once a walk takes the cell.
    CatchUp = tuple[asyncio.Future[Any], Callable[[], list[QueryMeasurement]]]
    CatchUpFn = Callable[[JobSearchPoint, Sample, str], CatchUp]

__all__ = [
    "Adapter",
    "Boundary",
    "CheckResult",
    "Controller",
    "Eliminator",
    "Measured",
    "MemberCoupling",
    "NodeMember",
    "OptimizerRuntime",
    "Panel",
    "Population",
    "Proposer",
    "Race",
    "RaceSnapshot",
    "ReviewReading",
    "ReviewStats",
    "RoundContext",
    "Sampler",
    "Selection",
    "Selector",
    "WorkingState",
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
class CheckResult:
    """One behaviour check an optimizer scores its own output on — scored, never gating."""

    check_id: str
    passed: bool
    evidence: str


class ReviewStats(Protocol):
    """The cycle-wide stats ``review.md`` prints; ``None`` on a rate is NOT MEASURED."""

    @property
    def yield_rate(self) -> float | None: ...
    @property
    def top_lift_mean(self) -> float | None: ...
    @property
    def behavior_pass_rate(self) -> float | None: ...
    @property
    def l2_behavior_pass_rate(self) -> float | None: ...
    @property
    def stagnation_max(self) -> int: ...
    @property
    def l2_fires(self) -> int: ...
    @property
    def round_1_verdict(self) -> str: ...


@dataclass(frozen=True)
class ReviewReading:
    """An optimizer's own reading of a cycle, which ``review.md`` renders. Every list holds one
    entry per round: its checks, the variants its proposer emitted, the feedback it closed on."""

    checks: list[list[CheckResult]]
    check_ids: tuple[str, ...]
    stats: ReviewStats
    variants: list[list[dict[str, Any]]]
    feedback: list[str]


class WorkingState(Protocol):
    """An optimizer's own state between rounds, minted by its ``OptimizerRuntime``. The bench
    carries it on ``Cycle.working_state``, asks it only these, and reads nothing inside it."""

    def origin_state(self, selected: SelectedOptimizer) -> OptimizerState:
        """What round 0's document banks."""
        ...

    def replay(self, last: RoundResult) -> None:
        """Take up the state ``last`` banked: a resume, a fork or a repair re-seats the cycle there."""
        ...

    def resume(self, ledger: CycleEventLog | None, selected: SelectedOptimizer) -> None:
        """Rebuild what the round documents do not bank, off the cycle's ledger, once a resume has
        replayed its priors."""
        ...

    def absorb(self, round_result: RoundResult) -> None:
        """Fold a closed round in, and stamp its document with the state it closed on."""
        ...

    def standing(self) -> tuple[int, int | None]:
        """The two numbers the round readout carries beside the round: rounds without advance, and
        the run's remaining allowance where the optimizer keeps one."""
        ...


class OptimizerRuntime(Protocol):
    """An optimizer's implementation beyond its nodes, registered under its manifest's ``name``:
    it mints the working state the bench carries."""

    @property
    def name(self) -> str: ...

    @property
    def own_axes(self) -> dict[str, set[str]]:
        """Node config keys the optimizer moves on ITSELF mid-run, by node — what the optimizer
        picture marks movable, where the manifest's ``param_keys`` declare none."""
        ...

    def start(
        self, session: Session, config: CampaignConfig, origin_results: list[dict[str, Any]]
    ) -> WorkingState: ...

    def prompt_hashes(self, selected: SelectedOptimizer) -> dict[str, str]:
        """Per llm node of ``selected``: what decides the text it sends. A round banks these, and a
        resume diverges at the first round whose stamp the optimizer loaded now does not match."""
        ...

    def complete(self) -> None:
        """Build the tables its members read, raising on a half-wired one — run where the bench
        completes every registry (``wiring.py::complete_registries``), never at import."""
        ...

    def source_digest(self, *covered: ModuleType) -> str:
        """The code that decides what its prompts SAY, digested; ``covered`` are modules another
        digest already hashes. An L4 inner cell's identity folds it in."""
        ...

    @property
    def checkpoint_gating(self) -> Mapping[ResumeCheckpointKind, GatingMode]:
        """Whether a resume re-derives each decision kind its members record, or only archives it."""
        ...

    @property
    def replayers(self) -> Mapping[str, Replayer]:
        """One per ``REPLAYED`` kind of its own — pure over one round, never the live cycle."""
        ...

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        """``{round: {node: fingerprint}}`` of what its nodes were handed, each rebuilt at its own
        point in the run — a repair that moves one forks the rounds downstream of it."""
        ...

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        session: Session,
        cycle: Cycle,
        drifted: list[RoundResult],
    ) -> None:
        """Re-derive, in place on disk, what each drifted round's nodes wrote from what they read."""
        ...

    def review(
        self,
        selected: SelectedOptimizer,
        rounds: list[RoundResult],
        audits: list[dict[str, Any] | None],
        *,
        context_object: list[str],
        origin_composite_fitness: float | None,
    ) -> ReviewReading | None:
        """``None`` where the optimizer keeps no reading of its own; ``review.md`` then says N/A."""
        ...


@dataclass(frozen=True)
class RoundContext:
    """One round as every node of its walk sees it."""

    cycle: Cycle
    round_num: int
    callbacks: RunCallbacks
    is_final_round: bool = False

    @property
    def state(self) -> WorkingState:
        return self.cycle.working_state


@dataclass(frozen=True)
class Panel:
    """The round's cells, and the order every arm walks them in. The parent's re-score reads
    ``cells``; the walks read ``order``, whose every ``block_size`` cells close a block — the
    points an eliminator decides at."""

    cells: list[Sample]
    order: list[Sample]
    block_size: int


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


@dataclass(frozen=True)
class RaceSnapshot:
    """One arm's mid-round standing, as an eliminator reports it to the run's callbacks.
    ``p_best`` is a SCALAR about ``current_id`` ALONE — a snapshot cannot answer a round-wide
    question; the per-prior numbers are in :attr:`paired_breakdown`."""

    p_best: float
    current_id: str
    n_samples: int
    paired_breakdown: dict[str, dict[str, float]]


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
    @property
    def abort_lenses(self) -> Mapping[str, frozenset[str]]:
        """Each ``abort:<variant>`` the mask's lens offers, by the gates it switches off — the
        values this eliminator stamps on ``elimination_context["gate"]``."""
        ...

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

    async def diagnose(self, ctx: RoundContext) -> None:
        """``--diag``: act on the round just closed, then show the next round's proposals
        without measuring them."""
        ...
