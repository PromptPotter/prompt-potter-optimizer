"""One Protocol per optimizer node type the bench walks a manifest through (`docs/developer/node-standard.md`)."""

from __future__ import annotations

import pkgutil
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
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

from promptpotter.config.paths import PACKAGE_ROOT
from promptpotter.config.prompt_blocks import PackShelf
from promptpotter.domain.arm_reading import ArmFit, DisplayMetric
from promptpotter.domain.arms import ArmSlot, Concluded, Elimination, Proposal, Proposed
from promptpotter.domain.campaign_config import ArmGateMode, CampaignConfig, Estimand, Knob
from promptpotter.domain.cell_fold import ScoreSummary
from promptpotter.domain.dashboard_rows import OptimizerLimit
from promptpotter.domain.opt_search_point import OptSearchPoint, VariationMode
from promptpotter.domain.optimizer_state import StatePart
from promptpotter.domain.optimizer_template import OptimizerPromptTemplate
from promptpotter.domain.origin_panel import election_rows
from promptpotter.domain.paired_reading import ArmKey, PriorStanding
from promptpotter.domain.phases import StopReason
from promptpotter.domain.pipeline_schema import INITIAL_POPULATION, NodeKind, PipelineSchema
from promptpotter.domain.pool import Access, DemoPool, Pool, PoolEntry
from promptpotter.domain.prompt_block import Presumption
from promptpotter.domain.results import OptimizerFact, RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell
from promptpotter.domain.strict_model import FrozenModel, StrictModel
from promptpotter.domain.validators import StopSignal
from promptpotter.shared.hashing import normalized_module, stable_hash
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.bench.cycle import ReadArm, RoundContext
    from promptpotter.application.bench.node_context import NodeContext
    from promptpotter.application.knobs import CouplingSeverity
    from promptpotter.application.optimizer_manifest import SelectedOptimizer
    from promptpotter.application.scoring.query_loop import BlockRace


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


class RewriteKnobs(StrictModel):
    rewrites: Annotated[str, Knob(Estimand.SEARCH)] = Field(
        min_length=1,
        description="Which loci of a prompt node the reply replaces. `prompt`: the whole prompt "
        "as one text — the model is shown every field rendered together, its reply lands in the "
        "field the node declares as its `prompt_info.rewrite_slot` and the node's other open "
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
    standing: str
    note: str
    arms: int | None
    proposer: str
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


def member_package(member: object) -> str | None:
    """The built-in optimizer package declaring *member*; ``None`` for a plugin's."""
    package = type(member).__module__.removeprefix(f"{_PACKAGE}.").split(".")[0]
    return package if package in BUILTIN_PACKAGES else None


# Generated by `scripts/offline_run.py` from each built-in optimizer's `BehaviourTrace` and
# re-derived by the gate's offline run, which is red on a diff: never hand-edited.
BEHAVIOUR_KEYS_FILE = PACKAGE_ROOT / "assets" / "behaviour_keys.json"


class BehaviourKeys(FrozenModel):
    world: str
    # By built-in optimizer: the hash of its straight run's trace in `world`.
    fingerprints: dict[str, str]
    # The hash of the L4 leg's trace, its own estimator read out (`runner/inner/tasks.py::estimator`).
    estimator: str

    @classmethod
    def held(cls) -> BehaviourKeys:
        return cls.model_validate_json(BEHAVIOUR_KEYS_FILE.read_bytes())


def _package_dir(member: NodeMember) -> Path:
    """The built-in optimizer package declaring *member*, or the top package of a plugin's."""
    package = member_package(member)
    top = f"{_PACKAGE}.{package}" if package else type(member).__module__.split(".")[0]
    return Path(str(sys.modules[top].__file__)).parent


def _files_hash(roots: Iterable[Path]) -> str:
    """Code less its prose (`normalized_module`), any other file as it stands; read live."""
    return stable_hash(
        {
            path.relative_to(root.parent).as_posix(): (
                normalized_module(text) if path.suffix == ".py" else text
            )
            for root in sorted(roots)
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path.suffix not in (".pyc", ".md")
            and (text := path.read_text(encoding="utf-8"))
        }
    )


def behaviour_key(name: str, members: Iterable[NodeMember], manifests: Sequence[Path]) -> str:
    """L4 identity (architecture §0.5): the code of the packages declaring its members and the
    files of the manifests it resolves from, its fingerprint on the offline world (`None` for a
    plugin's manifest) and the world's version. A built-in missing from `BEHAVIOUR_KEYS_FILE`
    raises: a stale key is a refused commit, never a second identity."""
    keys = BehaviourKeys.held()
    builtin = all(directory.is_relative_to(PACKAGE_ROOT) for directory in manifests)
    if builtin and name not in keys.fingerprints:
        raise LookupError(
            f"{BEHAVIOUR_KEYS_FILE.name} holds no {name!r}: give it any fingerprint there, and "
            "`scripts/offline_run.py` regenerates its own"
        )
    return stable_hash(
        {
            "code": _files_hash({_package_dir(member) for member in members}),
            "prompts": _files_hash(manifests),
            "fingerprint": keys.fingerprints[name] if builtin else None,
            "world": keys.world,
        }
    )


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
    paired_breakdown: dict[ArmKey, PriorStanding]


class Race(Protocol):
    """``judge`` reads a decided arm BEFORE ``admit`` makes it a prior; ``blocks`` stops every live walk at once.
    A race whose arms all walk one panel keeps no prior, so it inherits ``priors``, ``admit`` and ``catch_up``."""

    @property
    def n_priors(self) -> int: ...

    @property
    def priors(self) -> Mapping[ArmKey, CellSheet]:
        """Each prior a later arm is paired with, in prior order: the bench catches it up on a cell it lacks."""
        return {}

    @property
    def blocks(self) -> BlockRace[ArmKey]: ...

    @property
    def top_up(self) -> BlockRace[ArmKey] | None:
        """The race walked on over the origin panel's top-up; ``None`` where it ends with the draw."""
        return None

    def open_turn(self, key: ArmKey) -> None: ...

    def judge(
        self, signal: StopSignal | None, key: ArmKey, labels: Mapping[ArmKey, str]
    ) -> Elimination | None: ...

    def admit(self, _key: ArmKey, _results: Sequence[GradedCell]) -> None:
        """A decided arm's rows: it joins the priors."""
        return None

    def catch_up(self, _key: ArmKey, _cells: Sequence[GradedCell]) -> None:
        """A prior's caught-up cells, folded onto the rows it holds."""
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

    def race(self, ctx: NodeContext, panel: Panel) -> Race: ...


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

    def admission(self, _selected: SelectedOptimizer) -> float | None:
        """The ``P(θ_arm > θ_parent)`` ``select`` promotes at, which a race's leader lock-in stops at; ``None`` locks none in."""
        return None

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
