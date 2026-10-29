"""One Protocol per optimizer node type the bench walks a manifest through (`docs/developer/node-standard.md`)."""

from __future__ import annotations

import functools
import hashlib
import json
import pkgutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ClassVar,
    Literal,
    NamedTuple,
    Protocol,
    assert_never,
    cast,
    runtime_checkable,
)

from pydantic import Field

from promptpotter.application.scoring.formula.matchers import MATCHERS
from promptpotter.domain.arms import ArmSlot, Proposed
from promptpotter.domain.campaign_config import CampaignConfig, Estimand, Knob, Scope
from promptpotter.domain.optimizer_state import OptimizerState, StatePart, StateSlice
from promptpotter.domain.origin_panel import election_rows
from promptpotter.domain.pipeline_schema import NodeKind
from promptpotter.domain.pool import DemoPool
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.scoring import NO_CELLS, CellSheet
from promptpotter.domain.strict_model import StrictModel
from promptpotter.domain.validators import CatchUps
from promptpotter.infrastructure.store.source_scan import optimizer_prompt_shapers
from promptpotter.shared.hashing import module_source_digest
from promptpotter.shared.measurement_context import MeasurementRole

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
    from promptpotter.config.prompt_blocks import PackShelf
    from promptpotter.domain.arm_reading import ArmFit, DisplayMetric
    from promptpotter.domain.arms import Concluded, Elimination, Proposal
    from promptpotter.domain.campaign_config import ArmGateMode
    from promptpotter.domain.cell_fold import ScoreSummary
    from promptpotter.domain.dashboard_rows import OptimizerLimit
    from promptpotter.domain.opt_search_point import OptSearchPoint, VariationMode
    from promptpotter.domain.paired_reading import ArmKey
    from promptpotter.domain.phases import StopReason
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.pool import Access, Pool, PoolEntry
    from promptpotter.domain.results import OptimizerFact, RoundResult
    from promptpotter.domain.round_audit import RoundAudit
    from promptpotter.domain.sample import Sample
    from promptpotter.domain.scoring import GradedCell
    from promptpotter.domain.search_point import JobSearchPoint
    from promptpotter.domain.validators import StopRule, StopSignal

    # (the call already started, the commit writing its row once a walk takes the cell, the discard).
    CatchUp = tuple[asyncio.Future[Any], Callable[[], Sequence[GradedCell]], Callable[[], None]]
    CatchUpFn = Callable[[JobSearchPoint, Sample, ArmKey], CatchUp]


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


# How many of the pipeline's prompt nodes a member writes: a `one` refuses any other roster before the mint.
PromptArity = Literal["none", "one", "each"]

Rewrites = Literal["prompt", "field"]


class RewriteKnobs(StrictModel):
    rewrites: Annotated[Rewrites, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        description="Which loci of a prompt node the reply replaces. `prompt`: the whole prompt "
        "as one text — the model is shown every field rendered together, its reply rides `field` "
        "and the node's other fields empty, as the papers' one-text prompts do. `field`: that "
        "field alone — the model is shown it alone, and the child inherits every other locus "
        "from its first parent."
    )
    field: Annotated[str, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        min_length=1,
        description="The prompt field the reply is written into; every prompt node the member "
        "rewrites declares it, or the campaign is refused before it mints.",
    )


class ShotKnobs(StrictModel):
    k_max: Annotated[int, Knob(Scope.POLICY, Estimand.SEARCH)] = Field(
        ge=0,
        description="The most shots an individual this node writes may carry; 0 withdraws the "
        "demo pool from it.",
    )


@dataclass(frozen=True)
class AccessKnob:
    """A reader's access to a pool is the value of one ``Access`` knob, its own or a consulted member's."""

    knobs: type[StrictModel]
    knob: str

    def access(self, selected: SelectedOptimizer, member: NodeMember) -> Access:
        return cast("Access", getattr(selected.reached_knobs(member, self.knobs), self.knob))


@dataclass(frozen=True)
class CountKnob:
    """Shots are taken by id or not at all: a count above zero is ``closed``, zero is ``off``."""

    knobs: type[StrictModel]
    knob: str

    def count(self, selected: SelectedOptimizer, member: NodeMember) -> int:
        return cast("int", getattr(selected.reached_knobs(member, self.knobs), self.knob))

    def access(self, selected: SelectedOptimizer, member: NodeMember) -> Access:
        return "closed" if self.count(selected, member) > 0 else "off"


@dataclass(frozen=True)
class ShotCap:
    """The bench seat's draw: as many shots as any proposer of the round may draw (``SelectedOptimizer.shot_cap``)."""

    def access(self, selected: SelectedOptimizer, _member: NodeMember) -> Access:
        return "closed" if selected.shot_cap > 0 else "off"


# A manifest member's draw: one knob sets it.
KnobDraw = AccessKnob | CountKnob
Draw = KnobDraw | ShotCap


class CycleRead:
    """A typed read a member names in ``sees``, refused at load where ``refusal`` names why; ``NodeContext.read`` hands it over."""

    def __init__(self, round_: RoundContext, member: NodeMember) -> None:
        self._round = round_
        self._cycle = round_.cycle
        self._member = member

    @classmethod
    def refusal(cls, _member: NodeMember) -> str | None:
        return None


class NodeMember(Protocol):
    """Every edge to another member is declared here, and ``optimizer_manifest.py::_open_edges`` closes them before a mint; a member subclassing it states only what it holds."""

    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> NodeKind: ...

    knobs: ClassVar[type[StrictModel]] = StrictModel
    prompt_arity: ClassVar[PromptArity] = "none"
    reads: ClassVar[frozenset[type[StatePart]]] = frozenset()
    writes: ClassVar[frozenset[type[StatePart]]] = frozenset()
    # Every read beyond its own state, knobs and pools it is handed (`NodeContext.read`).
    sees: ClassVar[frozenset[type[CycleRead]]] = frozenset()
    # The pools it draws from, each under the draw that sets its access; read only through `NodeContext.drawn`.
    draws: ClassVar[Mapping[type[Pool], Draw]] = MappingProxyType({})
    # The modules deciding what its prompts SAY; `source_digest` hashes them beside its own module.
    prompt_sources: ClassVar[tuple[ModuleType, ...]] = ()
    # By decision kind, pure over one round; registering one is what makes a resume re-derive that kind.
    replayers: ClassVar[Mapping[str, Replayer]] = MappingProxyType({})
    couplings: ClassVar[tuple[MemberCoupling, ...]] = ()
    # `{row: count}` of declared surface no manifest shows, which `complexity_ledger` sums.
    priced_surface: ClassVar[Mapping[str, int]] = MappingProxyType({})

    @property
    def consults(self) -> frozenset[type]:
        """The members whose knobs it reads (`NodeContext.knobs`), and nothing else of theirs."""
        return frozenset()

    def limits(self, _selected: SelectedOptimizer) -> tuple[OptimizerLimit, ...]:
        return ()

    def round_facts(
        self, _selected: SelectedOptimizer, _round_result: RoundResult
    ) -> list[OptimizerFact]:
        """Read off the round file ALONE; the close stamps the roster's as ``RoundResult.optimizer_facts``."""
        return []

    def refuse_origin(self, _origin_results: CellSheet) -> None:
        """Raise where the origin's measurement holds what this member cannot start from."""
        return None

    def round_packages(self, _ctx: NodeContext) -> dict[str, str]:
        """``{node: fingerprint}`` of what the nodes it speaks for were handed in *ctx*'s round; a repair moving one forks the rounds after it."""
        return {}

    async def rederive(self, _ctx: NodeContext) -> tuple[StatePart, ...]:
        """What it wrote in *ctx*'s round, re-derived off the repaired rows; the bench restates it on the branch."""
        return ()

    def complete(self) -> None:
        """Run where the bench completes every registry (``wiring.py::complete_registries``), never at import."""
        return None


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
    feedback: list[str]


class SliceMap:
    """The state slices a cycle holds, one per kind its manifest carries, a written-first kind once its writer ran; the close banks them."""

    def __init__(self, kinds: Iterable[type[StateSlice]]) -> None:
        self._carried = {kind.kind: kind for kind in kinds}
        self._held: dict[str, StateSlice] = {
            name: kind() for name, kind in self._carried.items() if not kind.written_first()
        }

    def carries(self, kind: type[StateSlice]) -> bool:
        return self._carried.get(kind.kind) is kind

    def get[S: StateSlice](self, kind: type[S]) -> S:
        if not self.carries(kind):
            raise KeyError(f"the manifest carries no {kind.kind!r} slice")
        found = self._held.get(kind.kind)
        if not isinstance(found, kind):
            raise ValueError(f"the {kind.kind!r} slice is read before its writer ran")
        return found

    def put(self, value: StateSlice) -> None:
        if self._carried.get(value.kind) is not type(value):
            raise KeyError(f"the manifest carries no {value.kind!r} slice")
        self._held[value.kind] = value

    def held(self) -> dict[str, StateSlice]:
        return dict(self._held)

    def seat(self, banked: OptimizerState) -> None:
        """Take up what *banked* holds: a resume, a fork or a repair re-seats the cycle there."""
        unwritten = {k for k, kind in self._carried.items() if kind.written_first()}
        if not set(self._carried) - unwritten <= set(banked.slices) <= set(self._carried):
            raise ValueError(
                f"{banked.manifest!r} banked slices {sorted(banked.slices)}, "
                f"but its manifest carries {sorted(self._carried)}"
            )
        self._held = dict(banked.slices)


@dataclass(frozen=True)
class OptimizerPhase:
    phase: str
    node: str
    activity: str


class LlmNode(NodeMember):
    kind: ClassVar[NodeKind] = NodeKind.LLM
    # ``None`` where the node answers in free text; else ``build_optimizer_schemas.py`` writes its schema.
    response_model: ClassVar[type[BaseModel] | None] = None
    # Set where its call runs outside the manifest's ``default``.
    phase: ClassVar[OptimizerPhase | None] = None
    # Node config keys it moves mid-run, by the node it moves them on.
    steers: ClassVar[Mapping[str, frozenset[str]]] = {}
    # Params beyond the prompt fields an L4 override of this node carries, by JSON type.
    outer_levers: ClassVar[Mapping[str, str]] = {}

    def resolved_levers(self, _declared: Mapping[str, Any]) -> dict[str, Any]:
        """The L4 override levers its optimizer resolves itself, as they RESOLVE: the inner cell's identity reads them."""
        return {}


@dataclass(frozen=True)
class RoundOpening:
    standing: str
    note: str
    arms: int | None
    proposer: str
    replayed: bool


INITIAL_POPULATION = "initial_population"
RUN_INIT = "run_init"


@dataclass(frozen=True)
class RoundWalk:
    """``pipeline`` makes the round's arms; ``seeds`` walk ahead of it into the population it reads; ``gate`` says what of it walks."""

    pipeline: str
    seeds: tuple[Proposer, ...]
    proposers: tuple[Proposer, ...]
    gate: ArmGateMode

    @property
    def members(self) -> tuple[Proposer, ...]:
        return (*self.seeds, *self.proposers)

    @property
    def walked(self) -> tuple[tuple[Proposer, ...], tuple[Proposer, ...], bool]:
        """The seeds and proposers that walk, and whether the operator's seat takes the arms after them: under ``author``, in their place."""
        match self.gate:
            case "off":
                return self.seeds, self.proposers, False
            case "review":
                return self.seeds, self.proposers, True
            case "author":
                return (), (), True
            case unreachable:
                assert_never(unreachable)

    @property
    def steps(self) -> tuple[NodeMember, ...]:
        """``walked`` in the order ``runner/round.py::propose_population`` walks it."""
        seeds, proposers, seated = self.walked
        return (*seeds, *proposers, *((OPERATOR_SEAT,) if seated else ()))

    @property
    def seeding(self) -> bool:
        return bool(self.seeds) or self.pipeline == INITIAL_POPULATION

    @property
    def lead(self) -> Proposer:
        """The proposer making the round's arms, which states its opening; a seed ahead of it does not."""
        return self.proposers[0]

    @property
    def proposer(self) -> str:
        """Its first llm node, whose model the round's opening names."""
        return next(m.name for m in self.members if m.kind is NodeKind.LLM)


_PACKAGE = __name__.rpartition(".")[0]
BUILTIN_PACKAGES = frozenset(
    pkg.name for pkg in pkgutil.iter_modules([str(Path(__file__).parent)]) if pkg.ispkg
)


def member_package(member: object) -> str | None:
    """The built-in optimizer package declaring *member*; ``None`` for a plugin's."""
    package = type(member).__module__.removeprefix(f"{_PACKAGE}.").split(".")[0]
    return package if package in BUILTIN_PACKAGES else None


def source_digest(members: Iterable[NodeMember], *covered: ModuleType) -> str:
    """Every member's own module and its ``prompt_sources``, then the prompt shapers their packages hold; ``covered`` modules another digest already hashes."""
    held = list(members)
    own = {sys.modules[type(member).__module__] for member in held}
    spoken = {source for member in held for source in member.prompt_sources}
    roster = frozenset(p for member in held if (p := member_package(member)) is not None)
    return _source_digest(_by_name(own - spoken), _by_name(spoken), covered, roster)


def _by_name(modules: Iterable[ModuleType]) -> tuple[ModuleType, ...]:
    return tuple(sorted(modules, key=lambda module: module.__name__))


@functools.cache
def _source_digest(
    own: tuple[ModuleType, ...],
    spoken: tuple[ModuleType, ...],
    covered: tuple[ModuleType, ...],
    roster: frozenset[str],
) -> str:
    # The members' own modules are hashed whole; only `spoken` is scanned for the shapers it reads.
    foreign = frozenset(f"{_PACKAGE}.{package}" for package in BUILTIN_PACKAGES - roster)
    shapers = optimizer_prompt_shapers(spoken, covered=(*own, *covered), foreign=foreign)
    return module_source_digest(*spoken, *own, *shapers)


def rows_read(rounds: Sequence[RoundResult], round_num: int) -> str:
    """Every row of the rounds before *round_num*, so a repair drifts every round after the one it hit."""
    digest = hashlib.sha256()
    for rr in sorted(rounds, key=lambda r: r.round):
        if rr.round >= round_num:
            break
        for sheet in rr.cells.values():
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
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class RoundContext:
    """A member never sees it: each is handed the ``NodeContext`` the bench builds over it."""

    cycle: Cycle
    round_num: int
    callbacks: RunCallbacks
    is_final_round: bool = False
    # Each arm the round walked, its panel walk and every read of it as a reference, whoever asked.
    walks: dict[ArmKey, ArmWalk] = field(default_factory=dict, init=False)

    def asked(self, key: ArmKey, cells: Iterable[Sample]) -> frozenset[str]:
        """What a walk of *key* on *cells* is asked: those, and every cell its walk was asked before."""
        held = self.walks.get(key)
        return frozenset(s.key for s in cells) | (frozenset() if held is None else held.asked)

    def held(self, key: ArmKey) -> CellSheet:
        held = self.walks.get(key)
        return NO_CELLS if held is None else held.arm.rows

    def walked(self, read: ReadArm, asked: frozenset[str]) -> None:
        held = self.walks.get(read.key)
        self.walks[read.key] = ArmWalk(read, asked) if held is None else held.continued(read, asked)


@dataclass(frozen=True)
class Panel:
    """The parent's re-score reads ``cells``; the walks read ``order``, a block closing every ``block_size``."""

    cells: list[Sample]
    order: list[Sample]
    block_size: int


@dataclass(frozen=True)
class PanelSize:
    """A walk's ``Panel`` as counted BEFORE any round runs: no ``cells`` where the search split holds none."""

    cells: int
    block_size: int


@dataclass(frozen=True)
class Proposals:
    """The round's ARMS; the population carried between rounds is ``Selection.ranked``, a ``Survivor`` cutting it."""

    proposals: list[Proposal]

    @property
    def individuals(self) -> list[OptSearchPoint]:
        return [p.individual for p in self.proposals]

    def arms(self, round_num: int) -> dict[ArmKey, Proposed]:
        """An individual proposed twice is one arm, at its first slot."""
        arms: dict[ArmKey, Proposed] = {}
        for idx, p in enumerate(self.proposals):
            arms.setdefault(
                p.key(round_num, idx),
                Proposed(
                    role=MeasurementRole.PANEL,
                    slot=ArmSlot(round=round_num, idx=idx, total=len(self.proposals)),
                    individual=p.individual,
                    overlay=p.overlay,
                    validation_failures=p.validation_failures,
                ),
            )
        return arms


@dataclass(frozen=True)
class ReadArm:
    """An individual read on a round's cells as a reference: an arm of that round, with its rows."""

    key: ArmKey
    state: Concluded
    rows: CellSheet

    @property
    def individual(self) -> OptSearchPoint:
        return self.state.individual


@dataclass(frozen=True)
class ArmWalk:
    """One arm's walk of its round, which every later read of it continues: ``arm`` holds its rows,
    each read adding the cells they lack, and a panel arm's state; ``asked`` every cell asked of it."""

    arm: ReadArm
    asked: frozenset[str]

    def continued(self, read: ReadArm, asked: frozenset[str]) -> ArmWalk:
        held = frozenset(cell.key for cell in self.arm.rows)
        rows = self.arm.rows.merged(read.rows.where(functools.partial(_unheld, held)))
        state = read.state if self.arm.state.role == MeasurementRole.PARENT else self.arm.state
        return ArmWalk(ReadArm(read.key, state, rows), asked)


def _unheld(held: frozenset[str], cell: GradedCell) -> bool:
    return cell.key not in held


@dataclass(frozen=True)
class MeasuredRound:
    """A selector keeps from ``electable`` alone; ``cut`` is a budget stop on whose short panels the round still elects."""

    # The panel, in slot order.
    arms: dict[ArmKey, Concluded]
    rows: dict[ArmKey, CellSheet]
    parent: ReadArm
    # Every arm as the round files it, its walk's rows whole: the panel in slot order, then each reference.
    filed: dict[ArmKey, ReadArm]
    # Each filed arm's fold of those rows (`scoring/metrics.py::arm_folds`); none for an arm refused before a cell.
    folds: dict[ArmKey, ScoreSummary]
    fit: dict[ArmKey, ArmFit]
    electable: list[ArmKey]
    coverage_floor: int
    cut: StopReason | None
    # The origin panel by sample slot; `on_panel` holds each member's rows there beside its draw.
    origin_panel_slots: list[int]
    on_panel: dict[str, CellSheet]
    crowned: frozenset[str]

    @property
    def labels(self) -> dict[ArmKey, str]:
        return {key: arm.label for key, arm in self.arms.items()}

    def pooled(self, key: ArmKey) -> CellSheet:
        drawn = self.parent.rows if key == self.parent.key else self.rows[key]
        return election_rows(drawn, self.on_panel.get(key, NO_CELLS), self.crowned)


@dataclass(frozen=True)
class Selection:
    """``selected`` is ``None`` when the round holds its parent; ``leading`` is the selector's own, never re-ranked downstream."""

    selected: ArmKey | None
    leading: ArmKey | None
    verdict_reason: str
    # What the selector keeps, best first: the next population, cut by a survival node where the
    # walk names one (`Survivor`), else whole.
    ranked: Sequence[OptSearchPoint]


class Sampler(NodeMember, Protocol):
    @property
    def size_knob(self) -> str | None:
        """What a caller sizing the panel from outside (an L4 per-round count) writes; ``None`` where no one knob does."""
        ...

    def drawn(self, knobs: Any, search: int, pipeline: str) -> PanelSize:
        """The panel a walk of *pipeline* asks for under *knobs*; ``SelectedOptimizer.round_cells_ceiling`` prices each walk off it."""
        ...

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> Panel: ...


# What a proposer does with the proposals it is handed: `opens` ignores them and makes the round's
# arms, so it walks first alone; `adds` appends arms to them; `edits` rewrites them and adds none.
Proposing = Literal["opens", "adds", "edits"]


class Proposer(NodeMember, Protocol):
    """Each takes the proposals the one before it returned; the walk's first is handed none."""

    @property
    def proposes(self) -> Proposing:
        """A manifest is refused at load on a walk whose first neither opens nor adds, or that opens later."""
        ...

    @property
    def variation_mode(self) -> VariationMode:
        """How it makes a child, which every child it writes records."""
        ...

    def arms(self, _selected: SelectedOptimizer) -> int:
        """The arms it adds to its walk, whose arms are the sum and a round's the most of any walk it runs alone; an editor of arms another made adds none."""
        return 0

    def parent_read(self, _panel: PanelSize) -> int:
        """The cells of *panel* it reads the parent on (``Frontier.measure_parent``)."""
        return 0

    def opening(self, _ctx: NodeContext, bench: RoundOpening) -> RoundOpening:
        """Asked of the walk's ``lead``: the bench's own, *bench*, unless it keeps a reading of its own."""
        return bench

    async def propose(self, ctx: NodeContext, panel: Panel, proposals: Proposals) -> Proposals: ...


@dataclass(frozen=True)
class RaceSnapshot:
    """``p_best`` is a scalar about ``current`` ALONE: a snapshot answers no round-wide question."""

    p_best: float
    current: ArmKey
    n_samples: int
    paired_breakdown: dict[ArmKey, dict[str, float]]
    # Whether the cells read reached the depth this race lets a standing decide at.
    decision_grade: bool


class Race(CatchUps, Protocol):
    """``judge`` reads a decided arm BEFORE ``admit`` makes it a prior; ``rule`` stops one walk, ``blocks`` every live one."""

    @property
    def n_priors(self) -> int: ...

    @property
    def blocks(self) -> BlockRace[ArmKey] | None: ...

    def rule(self, ahead: Sequence[tuple[ArmKey, Walk | None]]) -> StopRule | None: ...

    def open_turn(self, key: ArmKey) -> None: ...

    def judge(
        self, signal: StopSignal | None, key: ArmKey, labels: Mapping[ArmKey, str]
    ) -> Elimination | None: ...

    def admit(self, key: ArmKey, results: Sequence[GradedCell], sp: JobSearchPoint) -> None: ...


class NoCatchUps:
    """The catch-up half of a ``Race`` whose arms all walk one panel, so no prior is paired."""

    def start_backfill(self, _sample: Sample, _room: int) -> list[asyncio.Future[Any]]:
        return []

    def owed_backfills(self, _sample: Sample) -> int:
        return 0

    def backfills_in_flight(self) -> list[asyncio.Future[Any]]:
        return []

    def backfills_for(self, _sample: Sample) -> list[asyncio.Future[Any]]:
        return []

    def commit_backfills(self, _sample: Sample) -> None:
        return None

    def bank_backfills(self, _samples: Sequence[Sample]) -> None:
        return None

    def discard_backfills(self) -> None:
        return None


class Eliminator(NodeMember, Protocol):
    @property
    def abort_lenses(self) -> Mapping[str, frozenset[str]]:
        """Each ``abort:<variant>`` the mask's lens offers, by the ``Elimination.context["gate"]`` values it switches off."""
        ...

    @property
    def stop_disqualifies(self) -> bool:
        """False where a stop only ends the buying of an arm the selector still reads; True takes it out of ``MeasuredRound.electable``."""
        ...

    def race(self, ctx: NodeContext, panel: Panel, catch_up: CatchUpFn) -> Race: ...


class Selector(NodeMember, Protocol):
    @property
    def elects_on(self) -> DisplayMetric:
        """The scoreboard column ``select`` elects on — the nearest the bench serves where its objective is its own."""
        ...

    @property
    def elects_partial(self) -> bool:
        """Whether ``select`` CAN elect on panels a budget stop cut short; whether the round MAY is ``panel_gate``'s. Neither, and the cut round is unwound."""
        ...

    def parent_cells(
        self, ctx: NodeContext, panel: Panel, rows: Mapping[ArmKey, CellSheet]
    ) -> list[Sample]:
        """The cells the bench re-scores the parent on: all an arm's lift against it can pair on."""
        ...

    def parent_read(self, panel: PanelSize) -> int:
        """The most of *panel*'s cells ``parent_cells`` names that no arm's walk buys."""
        ...

    def select(self, ctx: NodeContext, measured: MeasuredRound) -> Selection: ...


class Survivor(NodeMember, Protocol):
    """Survival, walked after the selector: what of its ranking the next round carries."""

    def survive(
        self, ctx: NodeContext, ranked: Sequence[OptSearchPoint]
    ) -> Sequence[OptSearchPoint]: ...


def admits(schema: PipelineSchema, matcher: str | None) -> frozenset[Presumption]:
    """The one admission rule: what a run's chain and its cell's matcher (`matchers.py::matcher_of`) let a block presume."""
    ranking = {Presumption.RANKING} if schema.emits_ranking else set()
    return frozenset(ranking) | (frozenset() if matcher is None else MATCHERS[matcher].admits)


class MintFacts(NamedTuple):
    """What a pool is refused against before a mint, and nothing at load knows."""

    demo: Sequence[Sample]
    admits: frozenset[Presumption]
    shelf: PackShelf


@runtime_checkable
class Initializer(NodeMember, Protocol):
    """A manifest's `run_init` walk: each writes its pool once, before round 0's adapters read it."""

    @property
    def pool(self) -> type[Pool]: ...

    def starting_content(
        self, selected: SelectedOptimizer, admits: frozenset[Presumption], shelf: PackShelf
    ) -> object:
        """What it can seed from beside the run's own data, which ``Treatment.source`` hashes: only what ``active`` keeps of what *admits* lets in off *shelf*."""
        ...

    def active(self, selected: SelectedOptimizer) -> list[str]: ...

    def held(self, selected: SelectedOptimizer, mint: MintFacts | None) -> list[PoolEntry]:
        """The entries an ``active`` path may name: at load with no *mint*, every one any run admits."""
        ...

    def refusal(
        self, selected: SelectedOptimizer, closed: bool, mint: MintFacts | None
    ) -> str | None:
        """At load with ``mint`` ``None``, again before the mint."""
        ...

    def initialize(self, ctx: NodeContext) -> None: ...


class Adapter(NodeMember, Protocol):
    """Writes off the round just absorbed; raises where it lands nothing, which the round survives and the next walk re-runs first."""

    async def adapt(self, ctx: NodeContext) -> None: ...


class Controller(NodeMember, Protocol):
    """Whether the run STOPS is the bench's (``runner/termination.py::standing_tripped``); ``act`` may still raise ``StopLoop``."""

    @property
    def fires(self) -> frozenset[type]:
        """The members it runs, each under its own context (``NodeContext.fire``)."""
        ...

    def observe(self, ctx: NodeContext, round_result: RoundResult) -> str | None:
        """Read the closed round into its own state; the rule that makes it act at this boundary, ``None`` where none does."""
        ...

    async def act(self, ctx: NodeContext, cause: str) -> None: ...

    async def diagnose(self, ctx: NodeContext) -> None:
        """``--diag``: act on the round just closed; the bench then shows the next round unmeasured."""
        ...

    async def show_round(self, ctx: RoundContext) -> None:
        """Propose *ctx*'s round and bank it UNMEASURED: ``--diag``'s last step."""
        ...


@runtime_checkable
class Reviewer(NodeMember, Protocol):
    """The member whose reading ``review.md`` prints; a roster holds at most one, and none says N/A."""

    def review(
        self,
        rounds: list[RoundResult],
        audits: list[RoundAudit | None],
        *,
        context_object: list[str],
    ) -> ReviewReading: ...


class OperatorSeat(NodeMember):
    """The arm gate's seat, the bench's own and no manifest's: an operator's arm is a child it admits as any other (`runner/arm_gate.py`)."""

    name: ClassVar[str] = "operator_seat"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    variation_mode: ClassVar[VariationMode] = "operator"
    draws: ClassVar[Mapping[type[Pool], Draw]] = MappingProxyType({DemoPool: ShotCap()})


OPERATOR_SEAT = OperatorSeat()
