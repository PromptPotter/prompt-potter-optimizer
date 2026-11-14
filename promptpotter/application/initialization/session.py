from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from promptpotter.application.run_phase_control import FlightGauge, RunControl
from promptpotter.application.scoring.evaluators import SampleEvaluator
from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import BankPartition
from promptpotter.domain.campaign import Optimize, Purpose
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.pipeline_params import PipelineParams
from promptpotter.domain.pipeline_schema import PipelineSchema
from promptpotter.domain.records.partition import PartitionRecord
from promptpotter.domain.records.run_records import (
    ArmAct,
    BackendRevision,
    PipelineRead,
)
from promptpotter.domain.sample import Bank
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.search_point import TaskDecomposition
from promptpotter.domain.spend import HeldBudget
from promptpotter.domain.validators import StopRule
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.run_observers import RunObservers


@dataclass(frozen=True)
class ScorerSetup:
    """Built whole by `scoring_scope.py::scorer_setup`; `{}` judges declares none, never "keep"."""

    scorer: Scorer
    display_metric: DisplayMetric
    partition: BankPartition
    degradation_checks: tuple[StopRule, ...]
    # One per declared term, built once per run so every judge validation precedes spend.
    judges: tuple[SampleEvaluator, ...]


@dataclass(frozen=True)
class LaunchClock:
    """One instant: ISO for the launch's record, monotonic for the wall-clock axis its book meters."""

    started_at: str
    monotonic: float

    @classmethod
    def now(cls) -> LaunchClock:
        return cls(started_at=utcnow_iso(), monotonic=time.monotonic())


@dataclass
class LaunchState:
    """Every field a launch moves after its ``Session`` is built; observers read it, never a copy."""

    # Read once at `run_optimization`'s start; every cycle its line closes reads this one.
    clock: LaunchClock | None = None
    resumed_from_round: int = 1
    # Seated once, before any cell is read: by run init (`runner/mint.py`) or a pass's opener.
    scoring: ScorerSetup = field(init=False)
    # Seeded off the cycle's index at init; afterwards written only by `act`.
    human_intervened: bool = False
    control: RunControl = field(default_factory=RunControl)
    flight: FlightGauge | None = None
    # What one phase view needs of an earlier one (`views/ingress.py`); a fork's session shares it.
    view_context: ViewContext = field(default_factory=ViewContext)
    # What admission held for this launch, set at the runner seam BEFORE the book is armed.
    held: HeldBudget | None = None
    # The cycle lock and projections the launch holds: `build_run_observers` moves them onto a fork.
    observers: RunObservers | None = None

    def launch_clock(self) -> LaunchClock:
        if self.clock is None:
            raise RuntimeError("a launch's clock is read only inside the `run_optimization` it set")
        return self.clock


@dataclass(frozen=True)
class Session:
    store: Stores
    backend_client: BackendClient
    pipeline_schema: PipelineSchema
    # Recorded whole by `record_resolved_run`, at the mint and at every `init_cycle`.
    pipeline_read: PipelineRead
    # What init read of the backend's revision; `None` where its connector checks none.
    backend_revision: BackendRevision | None = None
    # The label's bank at init; a bound cycle's is the one it drew, never re-cut.
    bank: Bank = field(default_factory=lambda: Bank.of(()))
    # Ranked beside the bank's ground truths; empty where a connector owns the panel.
    candidate_library: tuple[str, ...] = ()
    identity: IdentityContext = field(default_factory=default_identity)
    dataset_name: str | None = None
    # Where `dataset_name` resolved at init; `None` where a session measures no definition.
    dataset: ResolvedDataset | None = None
    pipeline_params: PipelineParams = field(default_factory=PipelineParams)
    # Per node, the `pipeline_params` keys hashed into identity and never sent to a backend.
    identity_keys: dict[str, frozenset[str]] = field(default_factory=dict)
    campaign_id: str = ""
    # Written by `bound` alone: a fork is a new session, never a moved field.
    cycle_id: str = ""
    # The bound campaign's declared purpose; a session binding none optimizes.
    purpose: Purpose = field(default_factory=Optimize)
    # What the bound cycle's chain drew; `None` exactly where no cycle is bound.
    drawn: BankPartition | None = None
    state: LaunchState = field(default_factory=LaunchState)

    @property
    def tenant_root(self) -> str:
        return str(self.store.base_dir)

    def record_resolved_run(self) -> None:
        self.store.campaigns.write_resolved_run(
            self.hop,
            self.pipeline_read,
            experiment=self.backend_client.workload.experiment,
            model_capabilities=self.pipeline_schema.model_capabilities,
        )

    def act(self, act: ArmAct) -> None:
        """decisions.md § 0005: the act is banked on the cycle, and every run it scores after grades C."""
        self.store.campaigns.act(self.hop, act)
        self.state.human_intervened |= act.babysits

    def bound(self, hop: CycleHop, *, purpose: Purpose) -> Session:
        """*purpose* is the campaign's declaration's; the bank and partition are what *hop*'s chain drew."""
        drawn = self.store.campaigns.last(hop, PartitionRecord)
        if drawn is None:
            raise ConflictError(
                f"cycle {hop.cycle_id} holds no drawn partition over its chain: its mint draws "
                "one and a fork stands on its parent's, so this cycle stands on neither."
            )
        bank = (
            self.bank
            if drawn.dataset_hash == self.bank.dataset_hash
            else self.store.tenant_datasets.bank(drawn.dataset_hash)
        )
        if bank is None:
            raise ConflictError(
                f"cycle {hop.cycle_id} drew bank {drawn.dataset_hash}, which is no longer held",
                code="campaign_roster_unreadable",
            )
        return replace(
            self,
            campaign_id=hop.campaign_id,
            cycle_id=hop.cycle_id,
            purpose=purpose,
            bank=bank,
            drawn=drawn.restored(bank.samples),
        )

    @property
    def hop(self) -> CycleHop:
        """Derived, never stored: a ``cycle_id`` repeats across sandboxes."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.cycle_id)

    @property
    def measured_dataset(self) -> str:
        if not self.dataset_name:
            raise RuntimeError("session.dataset_name is unset — this session measures no dataset.")
        return self.dataset_name

    @property
    def instrument_id(self) -> str:
        return instrument_of(self.bank.dataset_hash, self.pipeline_params)

    def points(self, framing: TaskDecomposition) -> PointContext:
        return PointContext(
            schema=self.pipeline_schema,
            framing=framing,
            demo=self.state.scoring.partition.demo,
        )
