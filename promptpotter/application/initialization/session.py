from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.run_phase_control import FlightGauge, RunControl
from promptpotter.domain.bench import BankPartition
from promptpotter.domain.campaign import Arm
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.results import DisplayMetric
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.spend import SpendCeilings
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store.stores import Stores
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
    judges: tuple[Evaluator, ...] = ()
    """The campaign's LLM-as-judge graders, already built into ``per_sample`` evaluators — one per
    declared TERM, empty where the campaign declared none. They live beside the compiled scorer
    because they are the same kind of fact — how this campaign grades a cell — and are built ONCE
    per run rather than per sample, so the registry lookup, the term validation and the spec
    validation all happen before any money is spent."""

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
    # Cells (`ReplayFeed.cell_key`) and calls (`call:{reuse key}`) the search already priced.
    priced_keys: set[str] = field(default_factory=set)


@dataclass
class Session:
    store: Stores
    backend_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema
    # The only copy of the live backend's declaration; `init_cycle` records it. Empty offline.
    pipeline_declaration: dict[str, Any] = field(default_factory=dict)
    samples: list[Sample] = field(default_factory=list)
    index_terms: list[str] = field(default_factory=list)
    identity: IdentityContext = field(default_factory=default_identity)
    dataset_name: str | None = None
    dataset_config_dir: Path | None = None
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
    arm: Arm | None = None

    @property
    def controlled(self) -> bool:
        return self.arm is not None

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
        return instrument_of(self.measured_dataset, self.pipeline_params)

    def llm_node_name(self) -> str:
        names = self.pipeline_schema.prompt_node_names()
        if not names:
            raise ValueError(
                f"dataset {self.dataset_name!r} declares no prompt-bearing node — "
                "cannot target a per-cell seed / model override"
            )
        return names[0]

    control: RunControl = field(default_factory=RunControl)
    flight: FlightGauge | None = None
    # ``HeldLimits.reserve``, set at the runner seam BEFORE the book is armed.
    reserve: SpendCeilings = field(default_factory=SpendCeilings)


__all__ = ["ScorerSetup", "Session"]
