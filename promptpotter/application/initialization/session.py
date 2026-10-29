from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from promptpotter.application.run_phase_control import FlightGauge, RunControl
from promptpotter.domain.arm_reading import DisplayMetric
from promptpotter.domain.arms import PointContext
from promptpotter.domain.bench import BankPartition, restored_partition
from promptpotter.domain.campaign import Compare, Optimize, Purpose
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.sample import Bank
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.scoring.evaluators import Evaluator
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.run_records import BackendRevision, ComparisonExit
    from promptpotter.domain.search_point import TaskDecomposition
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger


@dataclass(frozen=True)
class ScorerSetup:
    scorer: Scorer | None = None
    display_metric: DisplayMetric = "accuracy"
    partition: BankPartition | None = None
    degradation_checks: tuple[StopRule, ...] = ()
    # One per declared term, built once per run so every judge validation precedes spend.
    judges: tuple[Evaluator, ...] = ()

    def require_scorer(self) -> Scorer:
        if self.scorer is None:
            raise RuntimeError(
                "session.state.scoring is unset — populate_session_scoring must run first."
            )
        return self.scorer

    def require_partition(self) -> BankPartition:
        if self.partition is None:
            raise RuntimeError("session.state.scoring is unset — the bank was never split.")
        return self.partition


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
    audit_projection: AuditTrailProjection | None = None
    ledger: CycleEventLog | None = None
    scoring: ScorerSetup = field(default_factory=ScorerSetup)
    source: RunSource | None = None
    # Seeded off the cycle's index at init; afterwards written only by `record_intervention`.
    human_intervened: bool = False
    control: RunControl = field(default_factory=RunControl)
    # The forks this launch's line rebased onto: past `MAX_AUTO_REBASES` a proposal is dropped.
    rebases: int = 0
    flight: FlightGauge | None = None
    # What admission held for this launch, set at the runner seam BEFORE the book is armed.
    held: HeldBudget | None = None
    # The cycle whose producer lock the launch holds: `build_run_observers` moves it onto a fork.
    locked_cycle: CycleDir | None = None

    def launch_clock(self) -> LaunchClock:
        if self.clock is None:
            raise RuntimeError("a launch's clock is read only inside the `run_optimization` it set")
        return self.clock


@dataclass(frozen=True)
class Session:
    store: Stores
    backend_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema
    # The only copy of the live backend's declaration; `init_cycle` records it. Empty offline.
    pipeline_declaration: dict[str, Any] = field(default_factory=dict)
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
    pipeline_params: dict[str, Any] = field(default_factory=dict)
    # Per node, the `pipeline_params` keys hashed into identity and never sent to a backend.
    identity_keys: dict[str, frozenset[str]] = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None
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
            declaration=self.pipeline_declaration,
            experiment=self.backend_client.workload.experiment,
            model_capabilities=self.pipeline_schema.model_capabilities,
        )

    def leave_comparison(self, act: ComparisonExit, *, on: CycleHop) -> None:
        """decisions.md § 0009: the act is admitted; on an arm it is the event its guard reads."""
        if isinstance(self.purpose, Compare):
            self.store.campaigns.record_left_comparison(on, act=act)

    def record_intervention(self, kind: str) -> None:
        """decisions.md § 0005: the act is banked on the cycle, and every run it scores after grades C."""
        self.store.campaigns.record_intervention(self.hop, kind=kind)
        self.state.human_intervened = True

    def bound(self, hop: CycleHop, *, purpose: Purpose) -> Session:
        """*purpose* is the campaign's declaration's; the bank and partition are what *hop*'s chain drew."""
        drawn = self.store.campaigns.read_bank_partition(hop)
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
            drawn=restored_partition(bank.samples, drawn),
        )

    @property
    def index_terms(self) -> list[str]:
        truths = {s.ground_truth for s in self.bank.samples if s.ground_truth}
        return sorted(truths | set(self.candidate_library))

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
            demo=self.state.scoring.require_partition().demo,
        )
