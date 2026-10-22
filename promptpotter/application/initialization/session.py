from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.run_phase_control import FlightGauge, RunControl
from promptpotter.domain.bench import BankPartition
from promptpotter.domain.campaign import Optimize, Purpose
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.results import DisplayMetric
from promptpotter.domain.sample import Bank
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.spend import HeldBudget
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store.dataset_access import ResolvedDataset
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.errors import ConflictError
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.scoring.evaluators import Evaluator
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger


@dataclass
class ScorerSetup:
    scorer: Scorer | None = None
    display_metric: DisplayMetric = "accuracy"
    partition: BankPartition | None = None
    degradation_checks: list[StopRule] = field(default_factory=list)
    # One per declared term, built once per run so every judge validation precedes spend.
    judges: tuple[Evaluator, ...] = ()

    def require_scorer(self) -> Scorer:
        if self.scorer is None:
            raise RuntimeError(
                "session.scoring.scorer is unset — populate_session_scoring must run first."
            )
        return self.scorer

    def require_partition(self) -> BankPartition:
        if self.partition is None:
            raise RuntimeError("session.scoring.partition is unset — the bank was never split.")
        return self.partition


@dataclass
class CycleSnapshot:
    cycle_id: str = ""
    resumed_from_round: int = 1
    audit_projection: AuditTrailProjection | None = None
    ledger: CycleEventLog | None = None


@dataclass
class Session:
    store: Stores
    backend_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema
    # The only copy of the live backend's declaration; `init_cycle` records it. Empty offline.
    pipeline_declaration: dict[str, Any] = field(default_factory=dict)
    # The label's bank at init; a bound cycle's is the one it drew (`bind_campaign`), never re-cut.
    bank: Bank = field(default_factory=lambda: Bank.of(()))
    # Ranked beside the bank's ground truths; empty where a connector owns the panel.
    candidate_library: tuple[str, ...] = ()
    identity: IdentityContext = field(default_factory=default_identity)
    dataset_name: str | None = None
    # Where `dataset_name` resolved at init; `None` where a session measures no definition.
    dataset: ResolvedDataset | None = None
    tenant_root: str = ""
    pipeline_params: dict[str, Any] = field(default_factory=dict)
    # Per node, the `pipeline_params` keys hashed into identity and never sent to a backend.
    identity_keys: dict[str, frozenset[str]] = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None

    campaign_id: str = ""

    state: CycleSnapshot = field(default_factory=CycleSnapshot)
    scoring: ScorerSetup = field(default_factory=ScorerSetup)

    source: RunSource | None = None
    # An operator edited a locked value (ADR-0005): every run this cycle scores grades C.
    human_intervened: bool = False
    # The bound campaign's declared purpose; a session binding none optimizes.
    purpose: Purpose = field(default_factory=Optimize)

    def bind_campaign(self, hop: CycleHop, *, purpose: Purpose) -> None:
        """*purpose* is the campaign's declaration's; what the run remembers derives from it."""
        self.campaign_id = hop.campaign_id
        self.purpose = purpose
        self.state.cycle_id = hop.cycle_id
        drawn = self.store.campaigns.read_bank_partition(hop)
        if drawn is None or drawn.dataset_hash == self.bank.dataset_hash:
            return
        bank = self.store.tenant_datasets.bank(drawn.dataset_hash)
        if bank is None:
            raise ConflictError(
                f"cycle {hop.cycle_id} drew bank {drawn.dataset_hash}, which is no longer held",
                code="campaign_roster_unreadable",
            )
        self.bank = bank

    @property
    def index_terms(self) -> list[str]:
        truths = {s.ground_truth for s in self.bank.samples if s.ground_truth}
        return sorted(truths | set(self.candidate_library))

    @property
    def hop(self) -> CycleHop:
        """Derived, never stored: ``cycle_id`` flips on a fork and repeats across sandboxes."""
        return CycleHop(campaign_id=self.campaign_id, cycle_id=self.state.cycle_id)

    @property
    def measured_dataset(self) -> str:
        if not self.dataset_name:
            raise RuntimeError("session.dataset_name is unset — this session measures no dataset.")
        return self.dataset_name

    @property
    def instrument_id(self) -> str:
        return instrument_of(self.bank.dataset_hash, self.pipeline_params)

    control: RunControl = field(default_factory=RunControl)
    flight: FlightGauge | None = None
    # What admission held for this launch, set at the runner seam BEFORE the book is armed.
    held: HeldBudget | None = None
    # The launch's start on ``time.monotonic()``: the wall-clock axis runs from here.
    launched: float = field(default_factory=time.monotonic)
