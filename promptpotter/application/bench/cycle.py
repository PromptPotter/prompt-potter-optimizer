"""State an optimizer carries ACROSS rounds rides ``slices``, what one round produced ``outputs`` by round: a bare field does not survive a resume."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.bench.difficulty import DifficultyView
from promptpotter.application.bench.round_analysis import compute_round_diagnostics
from promptpotter.application.intelligence.indexes.axis import AxisIndex
from promptpotter.application.optimizer_manifest import SelectedOptimizer, select_optimizer
from promptpotter.application.optimizers.nodes import ReadArm, SliceMap
from promptpotter.application.scoring.metrics import arm_folds
from promptpotter.domain.arm_reading import ArmFit, DisplayMetric
from promptpotter.domain.arms import Broken, Concluded, Cut, PointContext, Proposal
from promptpotter.domain.cell_fold import ScoreSummary, merge_known_outcomes
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.opt_search_point import OptSearchPoint
from promptpotter.domain.optimizer_state import OptimizerState, RoundOutput
from promptpotter.domain.origin_panel import OverlapReading
from promptpotter.domain.paired_reading import ArmKey, ReadingState
from promptpotter.domain.phases import StopOutcome, stop_reason_outcome
from promptpotter.domain.pipeline_overlay import node_config_items
from promptpotter.domain.results import Election, RoundOutcome, RoundResult
from promptpotter.domain.results_health import compute_round_health
from promptpotter.domain.ruler import RulerStamp
from promptpotter.domain.run_records import ResumeCheckpointRecord
from promptpotter.domain.run_standing import RunStanding, declared_selection, measured_arm
from promptpotter.domain.search_point import JobSearchPoint, TaskDecomposition
from promptpotter.domain.wounds import RuntimeFailure, rf_dedup_key
from promptpotter.infrastructure.store.layout import campaign_cycles_dir, root_cycle_id
from promptpotter.infrastructure.store.read_model import Moment

if TYPE_CHECKING:
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.intelligence.indexes.sample import SampleIndex
    from promptpotter.domain.bench import BenchPasses
    from promptpotter.domain.campaign_config import CampaignConfig
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.scoring import GradedCell


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
    schema: PipelineSchema,
) -> RoundResult:
    key, arm, results = origin_arm.key, origin_arm.state, origin_arm.rows
    # An abort pads no error rows onto the tail.
    not_attempted = max(0, arm.expected_samples - len(results))
    origin = RoundResult(
        round=0,
        arms={key: arm},
        cells={key: results},
        folds=arm_folds({key: arm}, {key: results}, schema),
        election=Election(
            selected=key,
            leading=key,
            ended_on=key,
            reference=None,
            electable=0,
            fit=fit,
            elects_on=elects_on,
            ruler=ruler,
        ),
        health=compute_round_health(
            results=results.cells, prior_healths=[], is_origin=True, not_attempted=not_attempted
        ),
        overlap=OverlapReading.unpaired(ReadingState.SAME_INDIVIDUAL, 0, False),
        optimizer_state=optimizer_state,
    )
    if not results:
        return origin
    # Without them round 1 opens blind to the origin's per-sample failure pattern.
    return origin.model_copy(update={"diagnostics": compute_round_diagnostics(origin, schema)})


def _assert_overlay_preserved(
    sp: JobSearchPoint, session_pipeline_params: dict[str, Any] | None
) -> None:
    """``content_hash`` flips on an overlay edit only if those keys survive into ``sp.pipeline_params``."""
    sp_pp = sp.pipeline_params or {}
    for node, cfg in node_config_items(session_pipeline_params):
        missing = set(cfg) - set(sp_pp.get(node, {}))
        assert not missing, (
            f"overlay keys stripped from {node}: {sorted(missing)} — "
            "the origin must carry session.pipeline_params, not a sparse schema view"
        )


@dataclass
class Cycle:
    session: Session
    config: CampaignConfig
    slices: SliceMap
    difficulty: DifficultyView
    # By round, what each round not yet closed has produced, which its close banks (`close_state`).
    outputs: dict[int, dict[str, RoundOutput]] = field(default_factory=dict)
    _axes: AxisIndex | None = None
    _sibling_failures: list[RuntimeFailure] | None = None

    # ``rounds[0]`` IS the origin's measurement, built by ``start``, so it is never absent.
    rounds: list[RoundResult] = field(default_factory=list)
    population: list[OptSearchPoint] = field(default_factory=list)
    framing: TaskDecomposition = field(default_factory=TaskDecomposition)
    sample_index: SampleIndex | None = None
    pending_decisions: list[ResumeCheckpointRecord] = field(default_factory=list)
    bench_passes: BenchPasses | None = None
    # The last close's, set by the close and by a resume's replay; ``None`` until a close states one.
    standing: RunStanding | None = None
    # `measured_pool`'s fold, held while the closed rounds it read are the cycle's, and its θ by scale.
    _pool: tuple[tuple[int, ...], dict[str, tuple[OptSearchPoint, list[GradedCell]]]] | None = None
    _pool_theta: dict[tuple[int, str], float | None] = field(default_factory=dict)

    @classmethod
    def start(
        cls,
        origin: ReadArm,
        *,
        framing: TaskDecomposition,
        session: Session,
        config: CampaignConfig,
    ) -> Cycle:
        schema = session.pipeline_schema
        origin_sheet = origin.rows
        selected = select_optimizer(config.optimization)
        selected.refuse_origin(origin_sheet)
        slices = SliceMap(selected.slices)
        sp = session.points(framing).job(origin.individual)
        _assert_overlay_preserved(sp, session.pipeline_params)
        # Hashed here because the parent advances with every round's head.
        difficulty = DifficultyView.open(session, config, origin=sp.sp_hash(schema))
        reading = difficulty.scale.frontier(origin_sheet)
        origin_round = _origin_round(
            origin,
            fit={}
            if reading is None
            else {
                origin.key: ArmFit(
                    theta=reading.theta, theta_se=reading.se, theta_caveat=reading.caveat
                )
            },
            ruler=difficulty.stamp,
            optimizer_state=None,
            elects_on=selected.elects_on,
            schema=schema,
        )
        return cls(
            session=session,
            config=config,
            slices=slices,
            difficulty=difficulty,
            rounds=[origin_round],
            framing=framing,
        )

    @property
    def origin_round(self) -> RoundResult:
        return self.rounds[0]

    @property
    def optimizer(self) -> SelectedOptimizer:
        return select_optimizer(self.config.optimization)

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
        """Every individual a CLOSED round concluded, by id in the order first measured, with its
        cells across those rounds merged — folded once per close, never banked."""
        closed = self.closed_rounds
        # Keyed on the closed rounds' objects: `seat` swaps in a new copy, never edits one.
        read_from = tuple(id(rr) for rr in closed)
        if self._pool is not None and self._pool[0] == read_from:
            return self._pool[1]
        held: dict[str, tuple[OptSearchPoint, list[GradedCell]]] = {}
        for rr in closed:
            for key, arm in rr.arms.items():
                if isinstance(arm, Concluded):
                    # By individual, never arm key: a key names a slot, and readers ask by id.
                    individual = arm.individual
                    _, rows = held.get(individual.id, (individual, []))
                    held[individual.id] = (individual, merge_known_outcomes(rows, rr.rows(key)))
        self._pool = (read_from, held)
        self._pool_theta.clear()
        return held

    def pooled_theta(self, individual_id: str) -> float | None:
        """θ on the round's one judging fit over every cell the pool holds of it, read once per scale;
        ``None`` where it is unread or a caveat says it is not ability."""
        held = self.measured_pool().get(individual_id)
        memo = (id(self.difficulty.judging), individual_id)
        if memo not in self._pool_theta:
            read = None if held is None else self.difficulty.scale.frontier(held[1])
            self._pool_theta[memo] = None if read is None or read.caveat else read.theta
        return self._pool_theta[memo]

    def _seat_origin(self, parent: ReadArm, optimizer_state: OptimizerState | None) -> None:
        """The origin's θ is carried, not re-fit: round 0 closes on the scale it opened on."""
        election = self.origin_round.election
        assert election is not None, "round 0 elects the origin it adopted"
        self.rounds[0] = _origin_round(
            parent,
            fit=election.fit,
            ruler=election.ruler,
            optimizer_state=optimizer_state,
            elects_on=election.elects_on,
            schema=self.session.pipeline_schema,
        )

    def restamp_origin_round(self, parent: ReadArm) -> None:
        self._seat_origin(parent, self.origin_round.optimizer_state)

    def replay_priors(self, priors: list[RoundResult]) -> None:
        assert priors, "a cut at round 0 reopens the origin; a replay seats a closed round"
        from_round = min(rr.round for rr in priors)
        self.rounds = [rr for rr in self.rounds if rr.round < from_round] + sorted(
            priors, key=lambda rr: rr.round
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
        self.rounds = [origin]
        self._seat_origin(ReadArm(key, head, origin.rows(key)), None)
        self.population = []
        self.slices = SliceMap(self.optimizer.slices)
        self.difficulty.seat(origin.election.ruler)

    def seat(self, rr: RoundResult) -> RoundResult:
        slot = next(i for i, held in enumerate(self.rounds) if held.round == rr.round)
        self.rounds[slot] = rr
        return rr

    @property
    def closed_rounds(self) -> list[RoundResult]:
        """``at_offset`` is a round's close: one a stop caught between election and close is no result."""
        return [rr for rr in self.rounds if rr.round == 0 or rr.at_offset is not None]

    def unwind_unclosed(self) -> None:
        self.rounds = self.closed_rounds

    def absorb_round(self, rr: RoundResult) -> None:
        self.rounds.append(rr)

    @property
    def elected(self) -> tuple[RoundResult, Election]:
        """The last round that elected: a generation-only round ends on no arm."""
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

    def banked_proposals(self, round_num: int) -> list[Proposal] | None:
        session = self.session
        proposed = session.store.campaigns.round_proposals(session.hop, round_num)
        return None if proposed is None else list(proposed.proposals)

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
        """The ONE builder of a round's state: its slices as they stand, every output kind it carries."""
        if stray := sorted(r for r in self.outputs if r != round_num):
            raise ValueError(f"round {round_num} closes with outputs pending for rounds {stray}")
        selected = self.optimizer
        held = self._held(round_num)
        # A round 0 every launch re-closes keeps what an earlier close banked.
        banked = (
            {} if held is None or held.optimizer_state is None else held.optimizer_state.outputs
        )
        produced = {**banked, **self.outputs.pop(round_num, {})}
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
            self._axes = AxisIndex(self.sample_index)
        self._axes.refresh(self.closed_rounds)
        return self._axes

    def runtime_failures(self) -> list[RuntimeFailure]:
        """Concluded sibling forks' first, then this cycle's own rounds, each distinct config once."""
        if self._sibling_failures is None:
            self._sibling_failures = _sibling_runtime_failures(self.session)
        own = (rf for rr in self.rounds[1:] for rf in _cut_failures(rr))
        return _distinct([*self._sibling_failures, *own])
