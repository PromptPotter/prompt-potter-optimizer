"""State an optimizer carries ACROSS rounds rides ``slices``, what one round produced ``outputs`` by round: a bare field does not survive a resume."""

from __future__ import annotations

import functools
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from promptpotter.application.bench.difficulty import DifficultyView
from promptpotter.application.bench.round_analysis import compute_round_diagnostics, head_health
from promptpotter.application.datasets.loaders import sample_dataset
from promptpotter.application.intelligence.earned_blocks import CampaignSurvey
from promptpotter.application.intelligence.indexes.axis import AxisIndex
from promptpotter.application.intelligence.indexes.sample import SampleIndex
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.scoring.metrics import arm_folds
from promptpotter.application.scoring.selection import banked_lift_prior
from promptpotter.domain.arm_reading import ArmFit, DisplayMetric
from promptpotter.domain.arms import Broken, Concluded, Cut, PointContext
from promptpotter.domain.campaign import Compare
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cell_fold import ScoreSummary, merge_known_outcomes
from promptpotter.domain.crowning_bar import BAR_ADMISSION, CrowningBar
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState, RoundOutput, StateSlice
from promptpotter.domain.origin_panel import OverlapReading
from promptpotter.domain.paired_reading import ArmKey, ReadingState
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.records.run_records import ResumeCheckpointRecord, RoundProposedRecord
from promptpotter.domain.results import Election, RoundOutcome, RoundResult
from promptpotter.domain.ruler import RulerStamp
from promptpotter.domain.run_standing import RunStanding, declared_selection, measured_arm
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import NO_CELLS, CellSheet, GradedCell, Scorer
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.verification import BenchPasses
from promptpotter.domain.wounds import RuntimeFailure, rf_dedup_key
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id
from promptpotter.infrastructure.store.read_model import Moment
from promptpotter.shared.measurement_context import MeasurementRole

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session


def _sibling_runtime_failures(session: Session) -> list[RuntimeFailure]:
    """Only a sibling that reached a natural conclusion carries trustworthy failures — as of this cycle's mint, so a resume reads what its first process read."""
    # This campaign's own forks under either purpose, so an arm reads only its own line.
    campaigns = session.store.campaigns
    root = root_cycle_id(session.cycle_id)
    moment = Moment(campaigns.ledger(session.hop), 0)
    out: list[RuntimeFailure] = []
    cycles_dir = campaign_cycles_dir(campaigns.campaign_root_dir(session.campaign_id))
    for sibling in sorted(d.name for d in cycles_dir.iterdir() if d.is_dir()):
        if sibling == session.cycle_id or root_cycle_id(sibling) != root:
            continue
        hop = CycleHop(campaign_id=session.campaign_id, cycle_id=sibling)
        index = campaigns.load(hop, moment=moment)
        reason = None if index is None else index.stop_reason
        if reason is None or stop_reason_outcome(reason) is not StopOutcome.SUCCESS:
            continue
        rounds = campaigns.standing_rounds(hop, moment=moment).rounds
        out.extend(rf for at in sorted(rounds) if at > 0 for rf in _cut_failures(rounds[at].close))
    return out


def _cut_failures(rr: RoundOutcome) -> list[RuntimeFailure]:
    return [
        arm.reason.failure
        for arm in rr.panel.values()
        if isinstance(arm, Cut) and isinstance(arm.reason, Broken)
    ]


def _distinct(failures: Sequence[RuntimeFailure]) -> list[RuntimeFailure]:
    out: dict[tuple[str, str, str], RuntimeFailure] = {}
    for rf in failures:
        out.setdefault(rf_dedup_key(rf.model_dump(mode="json")), rf)
    return list(out.values())


def _origin_round(
    origin_arm: ReadArm,
    *,
    fit: dict[ArmKey, ArmFit],
    ruler: RulerStamp,
    optimizer_state: OptimizerState | None,
    elects_on: DisplayMetric,
    scorer: Scorer,
    schema: PipelineSchema,
) -> RoundResult:
    key, arm, results = origin_arm.key, origin_arm.state, origin_arm.rows
    origin = RoundResult(
        round=0,
        arms={key: arm},
        reference=None,
        cells={key: results},
        folds=arm_folds({key: arm}, {key: results}, scorer, schema),
        election=Election(
            selected=key,
            leading=key,
            ended_on=key,
            electable=0,
            fit=fit,
            elects_on=elects_on,
            ruler=ruler,
            bar=None,
        ),
        # An abort pads no error rows onto the tail: the cells never sent are the panel's shortfall.
        health=head_health(0, arm, results, picked=True, priors=()),
        overlap=OverlapReading.unpaired(ReadingState.SAME_INDIVIDUAL, 0, False),
        optimizer_state=optimizer_state,
    )
    if not results:
        return origin
    # Without them round 1 opens blind to the origin's per-sample failure pattern.
    return origin.model_copy(update={"diagnostics": compute_round_diagnostics(origin, schema)})


def _origin_fit(difficulty: DifficultyView, origin: ReadArm) -> dict[ArmKey, ArmFit]:
    """The origin read as a fresh floor, on the scale the cycle stands on."""
    reading = difficulty.scale.frontier(origin.rows)
    if reading is None:
        return {}
    return {
        origin.key: ArmFit(theta=reading.theta, theta_se=reading.se, theta_caveat=reading.caveat)
    }


def _assert_overlay_preserved(sp: JobSearchPoint, session_pipeline_params: PipelineParams) -> None:
    """``content_hash`` flips on an overlay edit only if those keys survive into ``sp.pipeline_params``."""
    for node, cfg in session_pipeline_params.nodes.items():
        missing = set(cfg.laid_keys()) - set(sp.pipeline_params.node(node).laid_keys())
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "the origin must carry session.pipeline_params, not a sparse schema view"
        )


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig
    # The launch's one selection (`runner/entry.py` scopes it): a manifest edit lands at the next launch.
    optimizer: SelectedOptimizer
    slices: SliceMap
    difficulty: DifficultyView
    # Read once at the cycle's open (`runner/mint.py::open_cycle`): what every round's earned blocks mine.
    survey: CampaignSurvey
    # By round, what each round not yet closed has produced, which its close banks (`close_state`).
    outputs: dict[int, dict[str, RoundOutput]] = field(default_factory=dict)
    _axes: AxisIndex | None = None
    _sibling_failures: list[RuntimeFailure] | None = None

    # ``rounds[0]`` IS the origin's measurement, built by ``start``, so it is never absent.
    _rounds: list[RoundResult] = field(default_factory=list)
    population: list[OptSearchPoint] = field(default_factory=list)
    framing: TaskDecomposition = field(default_factory=TaskDecomposition)
    sample_index: SampleIndex | None = None
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    bench_passes: BenchPasses | None = None
    # The last close's, set by the close and by a resume's replay; ``None`` until a close states one.
    standing: RunStanding | None = None
    # Bumped by every write to `_rounds`: the memos below key on it, never on a round's address.
    _rounds_version: int = 0
    # `measured_pool`'s fold at a version, and its θ by scale.
    _pool: tuple[int, dict[str, tuple[OptSearchPoint, list[GradedCell]]]] | None = None
    _pool_theta: dict[tuple[RulerStamp, str], float | None] = field(default_factory=dict)
    # `sealed_rounds`' copies at a version.
    _sealed: tuple[int, tuple[RoundResult, ...]] | None = None

    @classmethod
    def start(
        cls,
        origin: ReadArm,
        *,
        framing: TaskDecomposition,
        session: Session,
        config: CampaignConfig,
        optimizer: SelectedOptimizer,
        survey: CampaignSurvey,
    ) -> Cycle:
        sp = session.points(framing).job(origin.individual)
        _assert_overlay_preserved(sp, session.pipeline_params)
        cycle = cls(
            session=session,
            config=config,
            optimizer=optimizer,
            slices=SliceMap(optimizer.slices),
            survey=survey,
            # Hashed here because the parent advances with every round's head.
            difficulty=DifficultyView.open(
                session, config, origin=sp.sp_hash(session.pipeline_schema)
            ),
            framing=framing,
        )
        optimizer.refuse_pipeline(session.pipeline_schema)
        cycle.seat_origin(origin, None)
        return cycle

    def crowning_bar(self, admission: float | None) -> CrowningBar:
        """The ONE bar crown, advance and promotion read, at the selector's *admission* (a
        selector declaring none is read at ``BAR_ADMISSION``); the tenant's lift prior is read only
        where the switch reads it."""
        statistic = self.config.optimization.crowning_bar
        prior = (
            banked_lift_prior(self.survey, self.session.measured_dataset)
            if statistic == "empirical_bayes"
            else None
        )
        held = BAR_ADMISSION if admission is None else admission
        return CrowningBar(statistic=statistic, admission=held, prior=prior)

    def reopen_difficulty(self) -> None:
        """Round 0's view again, on the scorer its close locked."""
        origin = self.origin_round.head
        assert origin is not None, "round 0 ends on its origin"
        self.difficulty = DifficultyView.open(self.session, self.config, origin=origin.sp_hash)

    def rng(self, round_num: int, tag: str) -> random.Random:
        """A function of ``run_seed``, the arm's replicate, *round_num* and *tag* alone, so a resume
        redraws the same and two replicates of one head-to-head treatment draw apart."""
        purpose, seed = self.session.purpose, f"{self.config.run_seed}"
        if isinstance(purpose, Compare):
            seed = f"{seed}:{purpose.arm.replicate}"
        return random.Random(f"{seed}:{round_num}:{tag}")

    @property
    def origin_round(self) -> RoundResult:
        return self.rounds[0]

    def origin_cells(self, dataset: list[Sample]) -> list[Sample]:
        """The cells the origin's walk draws of *dataset*, as its first walk drew them (`origin.py`)."""
        return sample_dataset(dataset, self.config.sp_budget_origin)

    @property
    def selection(self) -> RoundResult:
        return declared_selection(self.rounds)

    @property
    def selected_sp(self) -> JobSearchPoint:
        picked = self.selection.head
        assert picked is not None, "a closed round names the individual it ended on"
        return self.points.measured(picked)

    @property
    def points(self) -> PointContext:
        return self.session.points(self.framing)

    def measured(self, individual_id: str) -> Concluded:
        """The individual's latest arm a round of this cycle concluded."""
        return measured_arm(self.rounds, individual_id)

    def measured_pool(self) -> dict[str, tuple[OptSearchPoint, list[GradedCell]]]:
        """Every individual a CLOSED round concluded, by id in the order first measured, with its cells merged."""
        if self._pool is not None and self._pool[0] == self._rounds_version:
            return self._pool[1]
        closed = self.closed_rounds
        held: dict[str, tuple[OptSearchPoint, list[GradedCell]]] = {}
        for rr in closed:
            for key, arm in rr.arms.items():
                if isinstance(arm, Concluded):
                    # By individual, never arm key: a key names a slot, and readers ask by id.
                    individual = arm.individual
                    _, rows = held.get(individual.id, (individual, []))
                    held[individual.id] = (individual, merge_known_outcomes(rows, rr.rows(key)))
        self._pool = (self._rounds_version, held)
        self._pool_theta.clear()
        return held

    def pooled_theta(self, individual_id: str) -> float | None:
        """θ on the round's one judging fit over every cell the pool holds of it; ``None`` where it is unread or not ability."""
        held = self.measured_pool().get(individual_id)
        memo = (self.difficulty.stamp, individual_id)
        if memo not in self._pool_theta:
            read = None if held is None else self.difficulty.scale.frontier(held[1])
            self._pool_theta[memo] = None if read is None or read.caveat else read.theta
        return self._pool_theta[memo]

    def seat_origin(self, origin: ReadArm, optimizer_state: OptimizerState | None) -> None:
        """Round 0 on *origin*'s rows, read as a floor and left open so it closes in place."""
        self.restate_rounds(
            [
                _origin_round(
                    origin,
                    fit=_origin_fit(self.difficulty, origin),
                    ruler=self.difficulty.stamp,
                    optimizer_state=optimizer_state,
                    elects_on=self.optimizer.elects_on,
                    scorer=self.session.state.scoring.scorer,
                    schema=self.session.pipeline_schema,
                ),
                *self._rounds[1:],
            ]
        )

    def replay_priors(self, priors: list[RoundResult]) -> None:
        assert priors, "a cut at round 0 reopens the origin; a replay seats a closed round"
        from_round = min(rr.round for rr in priors)
        self.restate_rounds(
            [rr for rr in self._rounds if rr.round < from_round]
            + sorted(priors, key=lambda rr: rr.round)
        )
        closed = self.rounds[-1].closed_state
        self.population = list(closed.population)
        self.slices.seat(closed)
        self.difficulty.seat(self.elected[1].ruler)

    def reopen_origin(self) -> None:
        """A fork cut at round 0 keeps the origin's measurement and none of what its close banked."""
        origin, head = self.origin_round, self.origin_round.head
        assert origin.election is not None and head is not None, "round 0 ends on its origin"
        key = origin.election.ended_on
        self.restate_rounds([origin])
        # On the scale round 0 opened on, so its θ re-reads as it was read.
        self.difficulty.seat(origin.election.ruler)
        self.seat_origin(ReadArm(key, head, origin.rows(key)), None)
        self.population = []
        self.slices = SliceMap(self.optimizer.slices)

    @property
    def rounds(self) -> Sequence[RoundResult]:
        return self._rounds

    def restate_rounds(self, rounds: list[RoundResult]) -> None:
        """The ONE writer of the rounds, so a memo keyed on the version reads no stale round."""
        self._rounds = rounds
        self._rounds_version += 1

    def seat(self, rr: RoundResult) -> RoundResult:
        slot = next(i for i, held in enumerate(self._rounds) if held.round == rr.round)
        self.restate_rounds([*self._rounds[:slot], rr, *self._rounds[slot + 1 :]])
        return rr

    @property
    def sealed_rounds(self) -> tuple[RoundResult, ...]:
        """Every round less its optimizer state, copied once per version of the rounds."""
        if self._sealed is None or self._sealed[0] != self._rounds_version:
            sealed = tuple(rr.model_copy(update={"optimizer_state": None}) for rr in self._rounds)
            self._sealed = (self._rounds_version, sealed)
        return self._sealed[1]

    @property
    def closed_rounds(self) -> list[RoundResult]:
        """``at_offset`` is a round's close: one a stop caught between election and close is no result."""
        return [rr for rr in self.rounds if rr.round == 0 or rr.at_offset is not None]

    def unwind_unclosed(self) -> None:
        closed = self.closed_rounds
        self.restate_rounds(closed)
        # An unclosed round's decisions go with it: its re-entry decides them again.
        held = {rr.round for rr in closed}
        self.pending_decisions = [d for d in self.pending_decisions if d.round in held]

    def absorb_round(self, rr: RoundResult) -> None:
        self.restate_rounds([*self._rounds, rr])

    @property
    def elected(self) -> tuple[RoundResult, Election]:
        """The last round that elected."""
        return next((rr, rr.election) for rr in reversed(self.rounds) if rr.election is not None)

    @property
    def parent(self) -> OptSearchPoint:
        """The individual the last election ended on: a HELD round ends on the parent itself."""
        head = self.elected[0].head
        assert head is not None, "an election ends on an arm it concluded"
        return head.individual

    @property
    def parent_point(self) -> JobSearchPoint:
        return self.points.job(self.parent)

    @property
    def parent_fold(self) -> ScoreSummary | None:
        """What the parent SCORED the round it last ended on, never a reading of the frontier."""
        return self.elected[0].fold

    @property
    def frontier(self) -> list[GradedCell]:
        """Each sample's latest cell, of whichever individual measured it last: never scored."""
        cells: list[GradedCell] = []
        for rr in self.rounds:
            cells = merge_known_outcomes(cells, rr.results)
        return cells

    def banked_walk(self, round_num: int) -> RoundProposedRecord | None:
        session = self.session
        held = session.store.campaigns.standing_rounds(session.hop, moment=None).proposals
        return None if (proposed := held.get(round_num)) is None else proposed.record

    def seat_walk(self, round_num: int, walked: OptimizerState) -> None:
        """A replayed walk runs no proposer: the cycle takes up the state its first walk left."""
        self.population = list(walked.population)
        self.slices.seat(walked)
        self.outputs[round_num] = dict(walked.outputs)

    def _held(self, round_num: int) -> RoundResult | None:
        return next((rr for rr in self.rounds if rr.round == round_num), None)

    def output[O: RoundOutput](self, kind: type[O], round_num: int) -> O:
        """What *round_num* produced of *kind*: banked once it closed, else pending, else ``kind()`` — never for a written-first kind."""
        held = self._held(round_num)
        if held is not None and held.optimizer_state is not None:
            return held.optimizer_state.output(kind)
        pending = self.outputs.get(round_num, {}).get(kind.kind)
        if pending is None and kind.written_first():
            raise KeyError(f"round {round_num} holds no {kind.kind!r}: its writer has not run")
        return kind() if pending is None else cast("O", pending)

    def emit(self, output: RoundOutput, round_num: int) -> RoundResult | None:
        """Pending until *round_num* closes; onto a round already closed it returns the restatement, banked before it is seated."""
        held = self._held(round_num)
        if held is None or held.optimizer_state is None:
            self.outputs.setdefault(round_num, {})[output.kind] = output
            return None
        return held.model_copy(update={"optimizer_state": held.optimizer_state.holding(output)})

    def close_state(self, round_num: int) -> OptimizerState:
        """`round_state`, the round's pending outputs taken: none may stand for another round."""
        if stray := sorted(r for r in self.outputs if r != round_num):
            raise ValueError(f"round {round_num} closes with outputs pending for rounds {stray}")
        state = self.round_state(round_num)
        self.outputs.pop(round_num, None)
        return state

    def round_state(self, round_num: int) -> OptimizerState:
        """The ONE builder of a round's state: its slices as they stand and every output kind it carries."""
        selected = self.optimizer
        held = self._held(round_num)
        # A round closed again on re-measured rows (`seat_origin`) keeps what its close banked.
        banked = (
            {} if held is None or held.optimizer_state is None else held.optimizer_state.outputs
        )
        produced = {**banked, **self.outputs.get(round_num, {})}
        return OptimizerState(
            manifest=selected.name,
            population=list(self.population),
            prompt_hashes=selected.prompt_hashes(),
            slices=self.slices.held(),
            outputs={
                kind.kind: produced[kind.kind] if kind.kind in produced else kind()
                for kind in selected.outputs
                if kind.kind in produced or not kind.written_first()
            },
        )

    def axes(self) -> AxisIndex | None:
        """Over the CLOSED rounds: a round's adapt reads the flips before its own, as a resume does."""
        if self.sample_index is None:
            return None
        if self._axes is None:
            unit = self.session.state.scoring.scorer.fitness_unit
            self._axes = AxisIndex(self.sample_index, self.session.pipeline_schema, unit)
        self._axes.refresh(self.closed_rounds)
        return self._axes

    def runtime_failures(self) -> list[RuntimeFailure]:
        """Concluded sibling forks' first, then this cycle's own rounds, each distinct config once."""
        if self._sibling_failures is None:
            self._sibling_failures = _sibling_runtime_failures(self.session)
        own = (rf for rr in self.rounds[1:] for rf in _cut_failures(rr))
        return _distinct([*self._sibling_failures, *own])


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
class RoundContext:
    """A member never sees it: each is handed the ``NodeContext`` the bench builds over it."""

    cycle: Cycle
    round_num: int
    ledger: CycleEventLog
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

    def cut_on(self, key: ArmKey, state: Cut) -> None:
        """Its race stopped it past its draw: the walk keeps its rows, the arm reads cut."""
        held = self.walks[key]
        self.walks[key] = ArmWalk(ReadArm(key, state, held.arm.rows), held.asked)


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
