"""One Protocol per optimizer node type the bench walks a manifest through (`docs/developer/node-standard.md`)."""

from __future__ import annotations

import functools
import hashlib
import json
import pkgutil
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from promptpotter.application.campaign_config import CampaignConfig, Estimand
from promptpotter.application.initialization.session import Session
from promptpotter.domain.optimizer_state import OptimizerState, RoundPayload
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.results import rounds_without_advance
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import CatchUps
from promptpotter.infrastructure.store.source_scan import optimizer_prompt_shapers
from promptpotter.shared.hashing import module_source_digest

if TYPE_CHECKING:
    import asyncio
    from types import ModuleType

    from pydantic import BaseModel

    from promptpotter.application.bench.cycle import Cycle
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.bench.resume_and_fork.replayers import Replayer
    from promptpotter.application.knobs import CouplingSeverity
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.run_callbacks import RunCallbacks
    from promptpotter.application.scoring.query_loop import BlockRace, Walk
    from promptpotter.domain.cycle_paths import CycleHop
    from promptpotter.domain.dashboard_rows import OptimizerLimit
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.results import (
        CandidateProposal,
        DisplayMetric,
        OptimizerFact,
        ReferenceReading,
        RoundResult,
        ScoredCandidate,
    )
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import CellSheet, GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule, StopSignal
    from promptpotter.infrastructure.store.campaign_store.store import CampaignStore

    # (the call already started, the commit writing its row once a walk takes the cell, the discard).
    CatchUp = tuple[asyncio.Future[Any], Callable[[], Sequence[GradedCell]], Callable[[], None]]
    CatchUpFn = Callable[[JobSearchPoint, Sample, str], CatchUp]

__all__ = [
    "BUILTIN_PACKAGES",
    "Adapter",
    "BankedState",
    "CheckResult",
    "Controller",
    "EliminationReading",
    "Eliminator",
    "LlmNode",
    "Measured",
    "MemberCoupling",
    "NoCatchUps",
    "NodeMember",
    "OptimizerPhase",
    "OptimizerRuntime",
    "Panel",
    "Proposals",
    "Proposer",
    "Race",
    "RaceSnapshot",
    "ReviewReading",
    "ReviewStat",
    "RoundContext",
    "RoundOpening",
    "Sampler",
    "Selection",
    "Selector",
    "WorkingState",
    "proposals_as",
    "round_state",
    "rows_read_packages",
    "standing_opening",
    "state_as",
]


@dataclass(frozen=True)
class MemberCoupling:
    """``predicate`` is True in the VIOLATING combination; its third argument is the manifest file's knobs, before any overlay."""

    name: str
    knobs: tuple[str, ...]
    bench_knobs: tuple[str, ...]
    estimand: Estimand
    relation: str
    consequence: str
    severity: CouplingSeverity
    predicate: Callable[[CampaignConfig, Any, Any], bool]


class NodeMember(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> NodeKind: ...
    @property
    def knobs(self) -> type[StrictModel]: ...


@dataclass(frozen=True)
class CheckResult:
    """Scored, never gating."""

    check_id: str
    passed: bool
    evidence: str


@dataclass(frozen=True)
class ReviewStat:
    """``value`` ``None`` is NOT MEASURED and renders as a dash."""

    name: str
    value: float | str | None
    spec: str = ""


@dataclass(frozen=True)
class ReviewReading:
    """Every list holds one entry per round."""

    checks: list[list[CheckResult]]
    check_ids: tuple[str, ...]
    verdict: ReviewStat | None
    stats: tuple[ReviewStat, ...]
    variants: list[list[dict[str, Any]]]
    feedback: list[str]


class WorkingState(Protocol):
    """The bench carries it on ``Cycle.working_state``, asks it only these, and reads nothing inside it."""

    def round_payload(self) -> RoundPayload:
        """Asked at the election and again at the close, which banks the answer — round 0's included."""
        ...

    def replay(self, last: RoundResult) -> None:
        """Take up the state ``last`` banked: a resume, a fork or a repair re-seats the cycle there."""
        ...

    def absorb(self, round_result: RoundResult) -> None:
        """Fold an elected round in; the round is a value this never writes."""
        ...


class BankedState[P: RoundPayload]:
    """For an optimizer whose state IS its payload: a FROZEN model a member replaces by assignment."""

    def __init__(self, payload: P) -> None:
        self.payload = payload

    def round_payload(self) -> P:
        return self.payload

    def replay(self, last: RoundResult) -> None:
        self.absorb(last)

    def absorb(self, round_result: RoundResult) -> None:
        self.payload = round_result.optimizer_state.payload_as(type(self.payload))


def state_as[P: RoundPayload](ctx: NodeContext[Any], kind: type[P]) -> BankedState[P]:
    state = ctx.state
    if not isinstance(state, BankedState) or not isinstance(state.payload, kind):
        raise TypeError(f"a member banking {kind.__name__} was handed {type(state).__name__}")
    return state


def round_state(
    selected: SelectedOptimizer, payload: RoundPayload, population: Sequence[OptSearchPoint]
) -> OptimizerState:
    """Prompt hashes are stamped as the round is BUILT, never at a re-save."""
    return OptimizerState(
        manifest=selected.name,
        population=list(population),
        prompt_hashes=selected.prompt_hashes(),
        payload=payload,
    )


@dataclass(frozen=True)
class OptimizerPhase:
    phase: str
    node: str
    activity: str


class LlmNode:
    kind: ClassVar[NodeKind] = NodeKind.LLM
    # ``None`` where the node answers in free text; else ``build_optimizer_schemas.py`` writes its schema.
    response_model: ClassVar[type[BaseModel] | None] = None
    # Set where its call runs outside the manifest's ``default``.
    phase: ClassVar[OptimizerPhase | None] = None
    # Node config keys it moves mid-run, by the node it moves them on.
    steers: ClassVar[Mapping[str, frozenset[str]]] = {}
    # Params beyond the prompt fields an L4 override of this node carries, by JSON type.
    outer_levers: ClassVar[Mapping[str, str]] = {}

    def resolved_levers(self, declared: Mapping[str, Any]) -> dict[str, Any]:
        """The L4 override levers its optimizer resolves itself, as they RESOLVE: the inner cell's identity reads them."""
        return {}


@dataclass(frozen=True)
class RoundOpening:
    standing: str
    note: str
    arms: int | None
    proposer: str
    replayed: bool


def standing_opening(ctx: RoundContext) -> RoundOpening:
    selected = ctx.cycle.optimizer
    return RoundOpening(
        standing=f"no advance {rounds_without_advance(ctx.cycle.rounds)}",
        note="",
        arms=selected.arms_per_round,
        proposer=selected.proposer,
        replayed=False,
    )


_PACKAGE = __name__.rpartition(".")[0]
BUILTIN_PACKAGES = frozenset(
    pkg.name for pkg in pkgutil.iter_modules([str(Path(__file__).parent)]) if pkg.ispkg
)


@functools.cache
def _source_digest(sources: tuple[ModuleType, ...], covered: tuple[ModuleType, ...]) -> str:
    own = sources[-1].__name__.removeprefix(f"{_PACKAGE}.").split(".")[0]
    foreign = frozenset(f"{_PACKAGE}.{package}" for package in BUILTIN_PACKAGES - {own})
    return module_source_digest(
        *sources, *optimizer_prompt_shapers(sources, covered=covered, foreign=foreign)
    )


class OptimizerRuntime(ABC):
    """Every non-abstract member answers "none": the bench reads the same member of every optimizer and branches on no name."""

    name: ClassVar[str]
    manifest_dir: ClassVar[Path]
    # The modules deciding what its prompts SAY, its own package's last: ``Treatment.source``.
    prompt_sources: ClassVar[tuple[ModuleType, ...]]
    # ``{row: count}`` of declared surface no manifest shows, which ``complexity_ledger`` sums.
    priced_surface: ClassVar[Mapping[str, int]] = {}
    # By decision kind, pure over one round; registering one is what makes a resume re-derive that kind.
    replayers: ClassVar[Mapping[str, Replayer]] = {}
    couplings: ClassVar[Mapping[str, tuple[MemberCoupling, ...]]] = {}

    @abstractmethod
    def start(
        self, session: Session, config: CampaignConfig, origin_results: CellSheet
    ) -> WorkingState:
        """Mint the working state the bench carries on ``Cycle.working_state``."""

    @abstractmethod
    def arms(self, selected: SelectedOptimizer) -> int:
        """The most arms one round races."""

    def round_cells_ceiling(self, selected: SelectedOptimizer, pool: int) -> int:
        """Overridden only where a round's walks are not every arm plus the parent on the sampler's draw."""
        return (self.arms(selected) + 1) * selected.round_cells(pool)

    def limits(self, selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        return ()

    def opening(self, ctx: RoundContext) -> RoundOpening:
        return standing_opening(ctx)

    def complete(self) -> None:
        """Run where the bench completes every registry (``wiring.py::complete_registries``), never at import."""
        return None

    def source_digest(self, *covered: ModuleType) -> str:
        """``covered`` are modules another digest already hashes."""
        return _source_digest(self.prompt_sources, covered)

    def round_packages(self, cycle: Cycle, rounds: list[RoundResult]) -> dict[int, dict[str, str]]:
        """``{round: {node: fingerprint}}`` of what its nodes were handed; a repair moving one forks the rounds after it."""
        return {}

    async def rederive(
        self,
        campaign_store: CampaignStore,
        hop: CycleHop,
        cycle: Cycle,
        rounds: list[RoundResult],
        drifted: list[int],
    ) -> list[RoundResult]:
        """Re-derives what the nodes of each *drifted* round wrote and restates it on *hop*'s ledger."""
        return rounds

    async def show_round(self, ctx: RoundContext) -> None:
        """Propose *ctx*'s round and bank it UNMEASURED: ``--diag``'s last step."""
        raise NotImplementedError(f"optimizer {self.name!r} shows no unmeasured round")

    def review(
        self,
        selected: SelectedOptimizer,
        rounds: list[RoundResult],
        audits: list[RoundAudit | None],
        *,
        context_object: list[str],
    ) -> ReviewReading | None:
        """``None`` where the optimizer keeps no reading of its own; ``review.md`` then says N/A."""
        return None

    def round_facts(
        self, selected: SelectedOptimizer, round_result: RoundResult
    ) -> list[OptimizerFact]:
        """Read off the round file ALONE; the close stamps it as ``RoundResult.optimizer_facts``."""
        return []


def rows_read_packages(
    rounds: Sequence[RoundResult], proposers: Sequence[str]
) -> dict[int, dict[str, str]]:
    """A round's package digests all rows before it, so a repair drifts every round after the one it hit."""
    out: dict[int, dict[str, str]] = {}
    digest = hashlib.sha256()
    for rr in sorted(rounds, key=lambda r: r.round):
        out[rr.round] = dict.fromkeys(proposers, digest.hexdigest()[:16])
        for sheet in (*rr.reference_results.values(), *rr.all_candidate_results.values()):
            seen = [
                [
                    cell.facts.sample_key,
                    cell.facts.predicted,
                    cell.grade.fitness,
                    cell.grade.objective,
                ]
                for cell in sheet
            ]
            digest.update(json.dumps(seen, default=str).encode("utf-8"))
    return out


@dataclass(frozen=True)
class RoundContext:
    """A member never sees it: each is handed the ``NodeContext`` the bench builds over it."""

    cycle: Cycle
    round_num: int
    callbacks: RunCallbacks
    is_final_round: bool = False
    # A fresh proposal of a round the cycle already ran: a proposer replays nothing and banks nothing.
    detached: bool = False


@dataclass(frozen=True)
class Panel:
    """The parent's re-score reads ``cells``; the walks read ``order``, a block closing every ``block_size``."""

    cells: list[Sample]
    order: list[Sample]
    block_size: int


@dataclass(frozen=True)
class Proposals:
    """The round's ARMS; the population carried between rounds is ``NodeContext.population``."""

    proposals: list[CandidateProposal]

    @property
    def individuals(self) -> list[OptSearchPoint]:
        return [p.opt_sp for p in self.proposals]


def proposals_as[T: Proposals](proposals: Proposals, kind: type[T]) -> T:
    if not isinstance(proposals, kind):
        raise TypeError(f"a member reading {kind.__name__} was handed {type(proposals).__name__}")
    return proposals


@dataclass(frozen=True)
class Measured:
    """A selector keeps from ``electable`` alone; ``cut`` is a budget stop on whose short panels the round still elects."""

    rows: dict[str, CellSheet]
    scores: list[ScoredCandidate]
    scored: list[OptSearchPoint]
    parent: ReferenceReading
    parent_rows: CellSheet
    references: dict[str, CellSheet]
    electable: list[OptSearchPoint]
    coverage_floor: int
    cut: StopReason | None


@dataclass(frozen=True)
class Selection:
    """``selected_id`` is empty when the round holds its parent; ``leading_id`` is the selector's own, never re-ranked downstream."""

    selected_id: str
    leading_id: str
    scores: list[ScoredCandidate]
    verdict_reason: str


class Sampler(NodeMember, Protocol):
    @property
    def size_knob(self) -> str | None:
        """What a caller sizing the panel from outside (an L4 per-round count) writes; ``None`` where no one knob does."""
        ...

    def draws(self, selected: SelectedOptimizer, pool: int) -> int:
        """Cells a round after the first asks for, read BEFORE any round runs; 0 where the pool holds no panel."""
        ...

    def draw(self, ctx: NodeContext[Any], pool: list[Sample]) -> Panel: ...


class Proposer(NodeMember, Protocol):
    """Each takes the proposals the one before it returned; the walk's first is handed none."""

    @property
    def opens(self) -> bool:
        """True of a walk's first proposer and of no other, which ``round_plan`` refuses a manifest on."""
        ...

    async def propose(
        self, ctx: NodeContext[Any], panel: Panel, proposals: Proposals
    ) -> Proposals: ...


@dataclass(frozen=True)
class RaceSnapshot:
    """``p_best`` is a scalar about ``current_id`` ALONE: a snapshot answers no round-wide question."""

    p_best: float
    current_id: str
    n_samples: int
    paired_breakdown: dict[str, dict[str, float]]
    # Whether `n_samples` reached the depth this race lets a standing decide at.
    decision_grade: bool


@dataclass(frozen=True)
class EliminationReading:
    """``reason`` is printed as served by every surface; ``context`` only the optimizer reads."""

    reason: str
    context: Mapping[str, Any]


class Race(CatchUps, Protocol):
    """``judge`` reads a decided arm BEFORE ``admit`` makes it a prior; ``rule`` stops one walk, ``blocks`` every live one."""

    @property
    def n_priors(self) -> int: ...

    @property
    def blocks(self) -> BlockRace | None: ...

    def rule(self, ahead: Sequence[tuple[str, Walk | None]]) -> StopRule | None: ...

    def open_turn(self, candidate_id: str, idx: int, n: int) -> None: ...

    def judge(
        self,
        signal: StopSignal | None,
        *,
        candidate_id: str,
        results: Sequence[GradedCell],
        labels: dict[str, str],
    ) -> EliminationReading | None: ...

    def admit(
        self, candidate_id: str, results: Sequence[GradedCell], sp: JobSearchPoint
    ) -> None: ...


class NoCatchUps:
    """The catch-up half of a ``Race`` whose arms all walk one panel, so no prior is paired."""

    def start_backfill(self, sample: Sample, room: int) -> list[asyncio.Future[Any]]:
        return []

    def owed_backfills(self, sample: Sample) -> int:
        return 0

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return []

    def backfills_for(self, sample: Sample) -> list[asyncio.Future[Any]]:
        return []

    def commit_backfills(self, sample: Sample) -> None:
        return None

    def bank_backfills(self, samples: Sequence[Sample]) -> None:
        return None

    def discard_backfills(self) -> None:
        return None


class Eliminator(NodeMember, Protocol):
    @property
    def abort_lenses(self) -> Mapping[str, frozenset[str]]:
        """Each ``abort:<variant>`` the mask's lens offers, by the ``elimination_context["gate"]`` values it switches off."""
        ...

    @property
    def stop_disqualifies(self) -> bool:
        """False where a stop only ends the buying of an arm the selector still reads; True takes it out of ``Measured.electable``."""
        ...

    def race(
        self, ctx: NodeContext[Any], panel: Panel, proposals: Proposals, catch_up: CatchUpFn
    ) -> Race: ...


class Selector(NodeMember, Protocol):
    @property
    def elects_on(self) -> DisplayMetric:
        """The scoreboard column ``select`` elects on — the nearest the bench serves where its objective is its own."""
        ...

    @property
    def elects_partial(self) -> bool:
        """Whether ``select`` can elect on panels a budget stop cut short; where it cannot, the cut round is unwound."""
        ...

    def parent_cells(
        self, ctx: NodeContext[Any], panel: Panel, rows: Mapping[str, CellSheet]
    ) -> list[Sample]:
        """The cells the bench re-scores the parent on: all an arm's lift against it can pair on."""
        ...

    def select(
        self, ctx: NodeContext[Any], measured: Measured, proposals: Proposals
    ) -> Selection: ...


class Adapter(NodeMember, Protocol):
    """Reads the round the cycle just absorbed and writes its working state, which the round's close banks."""

    async def adapt(self, ctx: NodeContext[Any], round_result: RoundResult) -> None: ...


class Controller(NodeMember, Protocol):
    """Whether the run STOPS is the bench's (``runner/termination.py::standing_tripped``); ``act`` may still raise ``StopLoop``."""

    def observe(self, ctx: NodeContext[Any], round_result: RoundResult) -> bool:
        """Read the closed round into its own state; whether it acts at this boundary."""
        ...

    async def act(self, ctx: NodeContext[Any]) -> None: ...

    async def diagnose(self, ctx: NodeContext[Any]) -> None:
        """``--diag``: act on the round just closed, then show the next round's proposals unmeasured."""
        ...
