from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.application.pipeline_resolve import resolved_dataset_name
from promptpotter.application.run_observers import build_campaign_emitter, declare_run_wiring
from promptpotter.application.run_phase_control import RunControl
from promptpotter.application.runner.campaign_ids import mint_campaign_id, mint_checkin_cycle_id
from promptpotter.domain.bench import BankPartition
from promptpotter.domain.campaign import Arm, Campaign, Treatment
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.measurement_provenance import RunSource
from promptpotter.domain.paired_reading import instrument_of
from promptpotter.domain.results import DisplayMetric
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import Scorer
from promptpotter.domain.spend import SpendCeilings
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.store.dataset_access import backend_type_of_dataset
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.session_pointer import save_active_pointer
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import utcnow_iso
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.jobs.mint import CyclePlan
    from promptpotter.application.scoring.evaluators import Evaluator
    from promptpotter.application.scoring.query_loop import FlightGauge
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
    from promptpotter.infrastructure.tracing.langfuse_client import LangfuseLogger


logger = logging.getLogger(__name__)


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


def mint_campaign(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    hop: CycleHop,
    label: str = "",
    treatment: Treatment,
    arm: Arm | None,
) -> None:
    """``hop.campaign_id`` is the CALLER's, so an L4 inner spawn lands back on a campaign it ran."""

    target_hash = hop.cycle_id.removeprefix("cycle_")
    validate_path_component(target_hash)
    now = utcnow_iso()
    dataset_name = resolved_dataset_name(session, campaign_config)
    validate_path_component(hop.campaign_id)
    root_cycle = hop.cycle_id

    campaigns = session.store.campaigns
    campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name=dataset_name,
            label=label,
            created_at=now,
            root_cycle_id=root_cycle,
            root_content_hash=target_hash,
            treatment=treatment,
            arm=arm,
            backend_id=session.backend_id,
            backend_url=session.backend_client.base_url,
            backend_type=backend_type_of_dataset(session.store, dataset_name),
            owner_user_id=str(session.identity.user_id),
            lifecycle_status="active",
            lifecycle_changed_at=now,
            config=campaign_config.frozen(arm=arm is not None),
        )
    )

    _declare_frozen_wiring(session, campaign_config, hop)
    campaigns.mint_cycle(hop)

    session.campaign_id = hop.campaign_id
    session.state.cycle_id = root_cycle

    save_active_pointer(session.store.base_dir, hop)

    # So the mint → loop-start window serves `dashboard.json` off the campaign's own declarations.
    build_campaign_emitter(session)

    logger.info("Minted fresh campaign %s — cycle %s", hop.campaign_id, root_cycle)


def _declare_frozen_wiring(
    session: Session, campaign_config: CampaignConfig, hop: CycleHop
) -> None:
    """Runs AHEAD of the record taking the cycle past check-in, so no read finds it stateless."""
    ledger = CycleEventLog.open(CycleDir(session.store.campaigns.cycle_dir(hop)))
    declare_run_wiring(session, campaign_config, ledger, tracing=None)


def mint_checkin_skeleton(stores: Stores, *, slug: str, backend_type: str) -> CycleHop:
    """Claims NO active pointer: an unrun check-in would pull a watching workspace off authoring."""

    now = utcnow_iso()
    hop = CycleHop(campaign_id=mint_campaign_id(slug), cycle_id=mint_checkin_cycle_id())

    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=hop.campaign_id,
            dataset_name=slug,
            created_at=now,
            root_cycle_id=hop.cycle_id,
            backend_id="",
            backend_type=backend_type,
            owner_user_id=str(stores.identity.user_id),
            lifecycle_changed_at=now,
            config={},
        )
    )
    stores.campaigns.mint_cycle(hop, checkin=True)

    logger.info("Minted check-in campaign %s — cycle %s", hop.campaign_id, hop.cycle_id)
    return hop


def finalize_checkin_to_active(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    hop: CycleHop,
    cycle_plan: CyclePlan,
) -> None:
    """The cycle id stays the provisional ``cycle_chk_*``: drift reads ``root_content_hash``."""

    target_hash = cycle_plan.cycle_id.removeprefix("cycle_")

    session.store.campaigns.update_campaign(
        hop.campaign_id,
        root_content_hash=target_hash,
        treatment=cycle_plan.treatment.model_dump(mode="json"),
        backend_id=session.backend_id,
        backend_url=session.backend_client.base_url,
        # Re-read, not trusted from the skeleton: the check-in writes `pipeline.yaml` in between.
        backend_type=backend_type_of_dataset(session.store, session.dataset_name or ""),
        config=campaign_config.frozen(arm=False),
    )
    session.campaign_id = hop.campaign_id
    session.state.cycle_id = hop.cycle_id

    # The store's OWN workspace: a sandboxed inner cycle (L4) never stamps the outer tenant's.
    save_active_pointer(session.store.base_dir, hop)

    _declare_frozen_wiring(session, campaign_config, hop)
    session.store.campaigns.close_checkin(hop)
    build_campaign_emitter(session)

    logger.info("Check-in campaign %s started — cycle %s", hop.campaign_id, hop.cycle_id)


def open_cycle_ledger(session: Session, cycle_id: str) -> CycleEventLog:
    cycle_dir = CycleDir(
        session.store.campaigns.cycle_dir(
            CycleHop(campaign_id=session.campaign_id, cycle_id=cycle_id)
        )
    )
    return CycleEventLog.open(cycle_dir)


__all__ = [
    "ScorerSetup",
    "Session",
    "finalize_checkin_to_active",
    "mint_campaign",
    "mint_checkin_skeleton",
]
