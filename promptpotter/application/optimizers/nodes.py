"""One Protocol per optimizer node type the bench walks a manifest through (`docs/developer/node-standard.md`)."""

from __future__ import annotations

import importlib
import pkgutil
import sys
import typing
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
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

from pydantic import BaseModel, Field

from promptpotter.application.scoring.race_protocol import BATCH, Decider, Trigger
from promptpotter.config.paths import PACKAGE_ROOT
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.domain.arm_reading import ArmFit, DisplayMetric
from promptpotter.domain.arms import ArmPosition, Concluded, Elimination, Proposal, Proposed
from promptpotter.domain.campaign_config import ArmGateMode, CampaignConfig, Estimand, Knob
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.crowning_bar import Vote
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.opt_search_point import OptSearchPoint, VariationMode
from promptpotter.domain.optimizer_state import StatePart
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.paired_reading import ArmKey
from promptpotter.domain.phase_acts import RowsRan, StepFact
from promptpotter.domain.pipeline_schema import (
    INITIAL_POPULATION,
    MEMBER_KINDS,
    NodeKind,
    PipelineSchema,
)
from promptpotter.domain.pool import Access, DemoPool, Pool, PoolEntry
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.records.run_records import StopLoop
from promptpotter.domain.results import OptimizerFact, RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import CellSheet, GradedCell
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.hashing import stable_hash
from promptpotter.shared.measurement_context import MeasurementRole
from promptpotter.shared.plugin_registry import Registry

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import ReadArm, RoundContext
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.config_map import CouplingSeverity
    from promptpotter.application.intelligence.rasch import RoundAbilities
    from promptpotter.application.optimizer_manifest import SelectedOptimizer


@dataclass(frozen=True)
class MemberCoupling:
    """``predicate`` is True in the VIOLATING combination; its third argument is the manifest file's knobs, before any overlay, its fourth the roster they run in. A ``refused`` one reads no bench knob, so every selection asks it (`optimizer_manifest.py::_select`), its config ``None``."""

    name: str
    knobs: tuple[str, ...]
    bench_knobs: tuple[str, ...]
    estimand: Estimand
    relation: str
    consequence: str
    severity: CouplingSeverity
    predicate: Callable[[CampaignConfig | None, Any, Any, SelectedOptimizer], bool]

    def __post_init__(self) -> None:
        assert self.severity != "refused" or not self.bench_knobs, (
            f"{self.name}: a refused coupling is asked at the selection, which reads no bench knob"
        )


# How many of the pipeline's prompt nodes a member writes: a `one` refuses any other roster before the mint.
PromptArity = Literal["none", "one", "each"]


class RewriteKnobs(StrictModel):
    rewrites: Annotated[str, Knob(Estimand.SEARCH)] = Field(
        min_length=1,
        description="Which loci of a prompt node the reply replaces. `prompt`: the whole prompt "
        "as one text — the model is shown every field rendered together, its reply lands in the "
        "field the node declares as its `prompt_info.rewrite_field` and the node's other open "
        "fields empty, as the papers' one-text prompts do. A field's name: that field alone — "
        "the model is shown it alone, and the child inherits every other locus from its first "
        "parent. Every prompt node the member rewrites leaves the field open, or the campaign is "
        "refused before it mints.",
    )


class ShotKnobs(StrictModel):
    k_max: Annotated[int, Knob(Estimand.SEARCH)] = Field(
        ge=0,
        description="The most shots an individual this node writes may carry; 0 withdraws the "
        "demo pool from it.",
    )


class LengthKnobs(StrictModel):
    length_budget: Annotated[int, Knob(Estimand.SEARCH)] = Field(
        ge=1,
        description=(
            "The most characters a child may lengthen or shorten its main parent's prompt by, "
            "summed over every field: a variant's edits and a crossover's donated loci alike, any "
            "child of the run; past it, either way, the child is refused. A parent is one "
            "generation from its own, so it also bounds the prompt every later call is shown."
        ),
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

    def refuse_pipeline(self, _schema: PipelineSchema) -> None:
        """Raise where the pipeline holds nothing this member can edit."""
        return None

    def complete(self) -> None:
        """Run where every registry completes (``maintenance/landing.py::complete_registries``)."""
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
    # Config keys beyond the call keys and prompt fields an overlay of this node may lay, by JSON type.
    outer_levers: ClassVar[Mapping[str, str]] = {}

    def resolved_levers(self, _declared: Mapping[str, Any]) -> dict[str, Any]:
        """Its laid levers as they RESOLVE, the one form its render and the inner cell's identity read."""
        return {}

    def lever_refusals(self, _declared: Mapping[str, Any]) -> list[str]:
        """Why its laid levers cannot render, which selection refuses (`optimizer_manifest.py::_select`)."""
        return []

    def unknown_slots(self, _template: OptimizerPromptTemplate) -> set[str]:
        """The ``{{slot}}`` names its running template holds and its call never fills, which selection refuses."""
        return set()


@dataclass(frozen=True)
class RoundOpening:
    """The walk's lead's facts on its round, by name, as a step's (`StepOpened.facts`)."""

    facts: dict[str, StepFact | None]
    arms: int | None
    replayed: bool


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


# Generated by `scripts/offline_run.py` from each built-in optimizer's `BehaviourTrace` and
# re-derived by the gate's offline run, which is red on a diff: never hand-edited.
BEHAVIOUR_KEYS_FILE = PACKAGE_ROOT / "assets" / "behaviour_keys.json"


class BehaviourKeys(FrozenModel):
    world: str
    # By built-in optimizer: the hash of its straight run's trace in `world` and of its member
    # packages' code that run cannot vouch for (`offline_run.py::unrun`).
    fingerprints: dict[str, str]
    # The hash of the L4 leg's inner campaigns in `world`, never its outer's own half.
    estimator: str

    @classmethod
    def held(cls) -> BehaviourKeys:
        return cls.model_validate_json(BEHAVIOUR_KEYS_FILE.read_bytes())

    @property
    def cell_estimator(self) -> str:
        """What reads an L4 cell's number (`InnerCells.estimator`): the L4 leg's inner behaviour in its world."""
        return stable_hash([self.estimator, self.world])


def member_package_dir(member: NodeMember) -> Path:
    """The built-in optimizer package declaring *member*, or the top package of a plugin's."""
    package = type(member).__module__.removeprefix(f"{_PACKAGE}.").split(".")[0]
    builtin = package in BUILTIN_PACKAGES
    top = f"{_PACKAGE}.{package}" if builtin else type(member).__module__.split(".")[0]
    return Path(str(sys.modules[top].__file__)).parent


@dataclass(frozen=True)
class RoundSamples:
    """The parent's re-score reads ``cells``; the walks read ``order``, a block closing every ``block_size``."""

    cells: list[Sample]
    order: list[Sample]
    block_size: int
    # The rest of the sampler's pool, its crowning cells dropped: what a race may take past `order`.
    pool: Sequence[Sample] = ()

    @property
    def walked(self) -> list[Sample]:
        """Every row a walk opens on; it takes ``len(order)`` of them, in the order its race chooses."""
        return [*self.order, *self.pool]

    def read_on(self, rows: Mapping[ArmKey, CellSheet]) -> list[Sample]:
        """``cells``, then each row of the pool an arm took past them: what the round reads a reference on."""
        taken = {cell.key for sheet in rows.values() for cell in sheet}
        return [*self.cells, *(s for s in self.pool if s.key in taken)]


@dataclass(frozen=True)
class DrawSize:
    """A walk's ``RoundSamples`` as counted BEFORE any round runs: no ``cells`` where the search split holds none."""

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
                    position=ArmPosition(round=round_num, idx=idx, total=len(self.proposals)),
                    individual=p.individual,
                    overlay=p.overlay,
                    validation_failures=p.validation_failures,
                ),
            )
        return arms


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
    # Every draw-electable arm's θ over `raced` against the parent: the one fit the election reads.
    abilities: RoundAbilities
    electable: list[ArmKey]
    coverage_floor: int
    cut: StopLoop | None
    # The origin panel by sample slot; `on_panel` holds each member's rows there beside its draw.
    origin_panel_slots: list[int]
    on_panel: dict[str, CellSheet]
    # Each arm and the parent as the race read them (`RoundResult.raced`): what θ is fit on.
    raced: dict[ArmKey, CellSheet]
    # The cells of `raced` the crowning bar pairs on (`Selector.vote`); `None` pairs on every one.
    paired_on: frozenset[str] | None

    @property
    def labels(self) -> dict[ArmKey, str]:
        return {key: arm.label for key, arm in self.arms.items()}


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

    def drawn(self, knobs: Any, search: int, pipeline: str) -> DrawSize:
        """The panel a walk of *pipeline* asks for under *knobs*; ``SelectedOptimizer.round_cells_ceiling`` prices each walk off it."""
        ...

    def draw(self, ctx: NodeContext, pool: list[Sample]) -> RoundSamples: ...


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

    def parent_read(self, _panel: DrawSize) -> int:
        """The cells of *panel* it reads the parent on (``Parent.measure``)."""
        return 0

    def opening(self, _ctx: NodeContext, bench: RoundOpening) -> RoundOpening:
        """Asked of the walk's ``lead``: the bench's own, *bench*, unless it keeps a reading of its own."""
        return bench

    async def propose(self, ctx: NodeContext, proposals: Proposals) -> Proposals: ...


@dataclass(frozen=True)
class RaceSnapshot:
    """``p_best`` is a scalar about ``current`` ALONE: a snapshot answers no round-wide question."""

    p_best: float
    current: ArmKey
    n_samples: int
    paired_breakdown: dict[ArmKey, float]


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


class Eliminator(NodeMember, Protocol):
    @property
    def abort_lenses(self) -> Mapping[str, frozenset[str]]:
        """Each ``abort:<variant>`` the mask's lens offers, by the ``Elimination.context["gate"]`` values it switches off."""
        ...

    @property
    def stop_disqualifies(self) -> bool:
        """False where a stop only ends the buying of an arm the selector still reads; True takes it out of ``MeasuredRound.electable``."""
        ...

    def race(self, ctx: NodeContext, panel: RoundSamples) -> Race: ...


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
        self, _ctx: NodeContext, panel: RoundSamples, rows: Mapping[ArmKey, CellSheet]
    ) -> list[Sample]:
        """The cells the bench re-scores the parent on: all an arm's lift against it can pair on."""
        return panel.read_on(rows)

    def parent_read(self, panel: DrawSize) -> int:
        """The most of *panel*'s cells ``parent_cells`` names that no arm's walk buys."""
        return panel.cells

    def admission(self, _selected: SelectedOptimizer) -> float | None:
        """The ``P(θ_arm > θ_parent)`` ``select`` promotes at, which a race's leader lock-in stops at; ``None`` locks none in."""
        return None

    def vote(self, _selected: SelectedOptimizer) -> Vote:
        """Which rows the crowning bar pairs an arm with its parent on, for ``select`` and the promotion alike."""
        return "draw"

    def select(self, ctx: NodeContext, measured: MeasuredRound) -> Selection: ...


class Survivor(NodeMember, Protocol):
    """Survival, walked after the selector: what of its ranking the next round carries."""

    def survive(
        self, ctx: NodeContext, ranked: Sequence[OptSearchPoint]
    ) -> Sequence[OptSearchPoint]: ...


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
        """What it can seed from beside the run's own data, which ``Treatment.behaviour`` hashes: only what ``active`` keeps of what *admits* lets in off *shelf*."""
        ...

    def active(self, selected: SelectedOptimizer) -> list[str]: ...

    def packs(self, selected: SelectedOptimizer) -> list[str] | None:
        """The block packs its pool draws from, in draw order; ``None`` where it draws from no shelf."""
        ...

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

    def bank_round(self, ctx: NodeContext, round_result: RoundResult) -> str | None:
        """Bank the closed round into its own state; the rule that makes it act at this boundary, ``None`` where none does."""
        ...

    async def act(self, ctx: NodeContext, cause: str) -> None: ...


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
    """The arm gate's seat, the bench's own and no manifest's: an operator's arm is a child it admits as any other (`runner/round.py::author_arm`)."""

    name: ClassVar[str] = "operator_seat"
    kind: ClassVar[NodeKind] = NodeKind.ALGORITHM
    variation_mode: ClassVar[VariationMode] = "operator"
    draws: ClassVar[Mapping[type[Pool], Draw]] = MappingProxyType({DemoPool: ShotCap()})


OPERATOR_SEAT = OperatorSeat()


ENTRY_POINT_GROUP = "promptpotter.optimizer_nodes"
"""Published, keyed by manifest node name: renaming it un-registers every plugin at once."""

_MEMBER_KINDS = MEMBER_KINDS | {NodeKind.LLM}


def _validate(obj: object, origin: str) -> NodeMember:
    name = getattr(obj, "name", None)
    if isinstance(obj, type) or not isinstance(name, str) or not name:
        raise RuntimeError(
            f"[{origin}] resolved to {obj!r}: a member is an instance naming the manifest node it "
            "answers in `name`."
        )
    where = f"optimizer node member {name!r} [{origin}]"
    if getattr(obj, "kind", None) not in _MEMBER_KINDS:
        raise RuntimeError(f"{where}: kind must be one of {sorted(_MEMBER_KINDS)}.")
    knobs = getattr(obj, "knobs", None)
    if not (isinstance(knobs, type) and issubclass(knobs, StrictModel)):
        raise RuntimeError(f"{where}: `knobs` must be a StrictModel subclass.")
    if getattr(obj, "prompt_arity", None) not in typing.get_args(PromptArity):
        raise RuntimeError(
            f"{where}: `prompt_arity` must be one of {typing.get_args(PromptArity)}."
        )
    for declared in ("reads", "writes"):
        parts = getattr(obj, declared, None)
        if not isinstance(parts, frozenset) or not all(
            isinstance(s, type) and issubclass(s, StatePart) and s.__dict__.get("kind")
            for s in parts
        ):
            raise RuntimeError(
                f"{where}: `{declared}` must be a frozenset of registered StatePart kinds."
            )
    sees = getattr(obj, "sees", None)
    if not isinstance(sees, frozenset) or not all(
        isinstance(s, type) and issubclass(s, CycleRead) for s in sees
    ):
        raise RuntimeError(f"{where}: `sees` must be a frozenset of CycleRead kinds.")
    member = typing.cast("NodeMember", obj)
    if refused := sorted(why for kind in sees if (why := kind.refusal(member)) is not None):
        raise RuntimeError(f"{where}: " + "; ".join(refused) + ".")
    consults = getattr(obj, "consults", None)
    if not isinstance(consults, frozenset) or not all(isinstance(c, type) for c in consults):
        raise RuntimeError(f"{where}: `consults` must be a frozenset of member classes.")
    reached = {knobs, *(getattr(c, "knobs", None) for c in consults)}
    draws = getattr(obj, "draws", None)
    if not isinstance(draws, Mapping) or not all(
        isinstance(pool, type)
        and issubclass(pool, Pool)
        and pool.__dict__.get("kind")
        and isinstance(draw, KnobDraw)
        and draw.knobs in reached
        and draw.knob in draw.knobs.model_fields
        for pool, draw in draws.items()
    ):
        raise RuntimeError(
            f"{where}: `draws` must map registered Pool kinds to an AccessKnob or CountKnob "
            "naming a knob of its own or a consulted member's knobs."
        )
    if defaulted := sorted(n for n, f in knobs.model_fields.items() if not f.is_required()):
        raise RuntimeError(
            f"{where}: knobs {defaulted} carry a default. The manifest declares every value, so "
            "a code default would be a second source for one number."
        )
    return member


def _members() -> Iterator[typing.Any]:
    for package in sorted(BUILTIN_PACKAGES):
        yield importlib.import_module(f"{__package__}.{package}.members")


def _builtins() -> Iterator[tuple[str, object]]:
    for module in _members():
        for obj in module.MEMBERS:
            yield module.__name__, obj


NODE_MEMBERS = Registry(ENTRY_POINT_GROUP, _builtins, _validate)


def llm_nodes() -> Mapping[str, LlmNode]:
    return {name: found for name, found in NODE_MEMBERS.table.items() if isinstance(found, LlmNode)}


def unfit_pipeline(
    selected: SelectedOptimizer, schema: PipelineSchema, connector: str
) -> str | None:
    """Why *selected* cannot write the prompt nodes *connector* declares in *schema*, asked before
    a mint spends; ``None`` where it can."""
    prompt_nodes = schema.prompt_nodes()
    if not any(n.open_prompt_fields for n in prompt_nodes) and not any(
        schema.node_param_keys().values()
    ):
        prompts = (
            "every prompt field is held"
            if prompt_nodes
            else f"no node it runs {list(schema.running)} declares a prompt"
        )
        return (
            f"this campaign leaves its optimizer nothing to edit: {prompts} and no config axis is "
            "open on any node, so each variant would be a no-op. Open a prompt field "
            "(`prompt_fields_held`) or a config axis (`param_keys`) in `optimizer_narrowing`."
        )
    roster = [node.name for node in prompt_nodes]
    for name in selected.member_nodes:
        arity = NODE_MEMBERS.get(name).prompt_arity
        if arity == "one" and len(prompt_nodes) != 1:
            return (
                f"optimizer {selected.name!r} node {name!r} rewrites the pipeline's one prompt "
                f"node, and this pipeline's chain has {len(roster)}: {roster}."
            )
        knobs = selected.knobs(name)
        if arity == "none" or not isinstance(knobs, RewriteKnobs):
            continue
        for node in prompt_nodes:
            if knobs.rewrites != "prompt":
                slot: str | None = knobs.rewrites
            elif (slot := node.prompt_info.rewrite_field if node.prompt_info else None) is None:
                return (
                    f"optimizer {selected.name!r} node {name!r} rewrites node {node.name!r}'s "
                    f"whole prompt, and connector {connector!r} declares no field it lands in "
                    "(`prompt_info.rewrite_field`)."
                )
            if slot not in node.open_prompt_fields:
                return (
                    f"optimizer {selected.name!r} node {name!r} writes {slot!r} into node "
                    f"{node.name!r} on connector {connector!r}, which "
                    f"{'holds' if slot in node.prompt_fields else 'does not declare'} it — set "
                    f"`optimization.nodes.{name}.config.rewrites` to an open field."
                )
    return None
