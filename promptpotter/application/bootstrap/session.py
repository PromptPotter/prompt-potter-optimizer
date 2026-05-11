"""Session/Cycle identity + ``active_session.json`` claim.

Owns ``Session``, the per-cycle bundles it carries (``CampaignBundle``,
``ScoringContext``), the tenant context, and the identity helpers used to
mint a session, claim the active pointer, and open per-cycle ledgers.
Wiring of stores/clients/datasets lives next door in :mod:`wiring`; the
scoring-context builder + cycle bootstrapper live in
:mod:`scoring_context`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import RoundScorer, Scorer
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store import (
    Stores,
    mint_session_id,
    save_active_pointer,
)
from promptpotter.infrastructure.store.base import validate_path_component

if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes import SampleIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections import AuditTrailView
    from promptpotter.infrastructure.tracing import LangfuseLogger, ObservabilityBridge


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ScoringContext:
    """Per-cycle scoring policy + active scoring set; populated post-Session-init."""

    scorer: Scorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    round_scorer: RoundScorer | None = None
    scorer_round_formula: str | None = (
        None  # None = registry default; else what scoring_steer.json replaced.
    )
    scoring_set: list[Sample] = field(default_factory=list)
    sample_index: SampleIndex | None = None
    degradation_checks: list[StopRule] = field(default_factory=list)


@dataclass
class CampaignBundle:
    """Per-cycle mutable bundle — flips on fork; ledger is the sole event ingress.

    Bundle of objects (ledger, projection writer, observability bridge), not a
    state machine. ``EscalationState`` is the FSM; this is plumbing.
    """

    cycle_id: str = ""
    tracing_campaign_id: str = ""
    # Next L1 round_num to execute. Origin = round 0 (always already done by
    # the time the round loop starts); first L1 round is 1. Fresh ⇒ 1,
    # resumed ⇒ ``len(prior_l1_rounds) + 1``.
    resumed_from_round: int = 1
    obs: ObservabilityBridge | None = None
    audit_projection: AuditTrailView | None = None
    ledger: CycleEventLog | None = None


@dataclass
class Session:
    """Session-scoped wiring + identity + per-cycle bundles."""

    # -- Wiring ----------------------------------------------------------
    store: Stores
    backend_id: str
    experiment_id: str  # Backend-side experiment id (TermNorm vocabulary). Distinct from cycle_id.
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    backend_index_synced: bool
    samples: list[Sample] = field(default_factory=list)
    experiment_extract: dict = field(default_factory=dict)
    index_terms: list[str] = field(default_factory=list)
    tenant: TenantContext | None = None
    dataset_name: str | None = None
    project_root: str = ""
    pipeline_params: dict = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None

    # -- Identity --------------------------------------------------------
    session_id: str = ""

    # -- Per-cycle bundles ----------------------------------------------
    state: CampaignBundle = field(default_factory=CampaignBundle)
    scoring: ScoringContext = field(default_factory=ScoringContext)

    # -- Runtime config --------------------------------------------------
    max_consecutive_errors: int = 3
    stale_data_load_protocol: list[str] | None = None
    source: str = ""

    # -- Lifecycle hook --------------------------------------------------
    stop_check: Callable[[], bool] | None = None


def new_session_state(
    *,
    init_params: dict,
    campaign_config: dict,
    pipeline_params: dict,
    active_steps: list[str],
) -> dict[str, Any]:
    """Fresh campaign session-state dict (shared by CLI init + orchestrator)."""
    return {
        "phase": "init",
        "init_params": init_params,
        "campaign_config": campaign_config,
        "pipeline_params": pipeline_params,
        "active_steps": active_steps,
        "origin_prompt_fields": {},
        "dataset_count": 0,
        "origin_accuracy": 0.0,
        "task_context": None,
        "experiment_id": None,
    }


def _build_index_header(session: Session, dataset_size: int) -> dict[str, Any]:
    """index.json header — tool/version/pipeline/backend/dataset identity."""
    from promptpotter.config.settings import APP_VERSION

    schema = session.pipeline_schema
    nodes = list(schema.nodes) if schema else []
    return {
        "tool": "promptpotter",
        "version": APP_VERSION,
        "n_nodes": len(nodes),
        "steps": [n.name for n in nodes],
        "backend_url": session.backend_client.base_url,
        "backend_id": session.backend_id,
        "dataset_name": session.dataset_name,
        "dataset_size": dataset_size,
    }


def auto_mint_session(
    session: Session,
    campaign_config: Any,
    *,
    cycle_id: str,
    origin_acc: float = 0.0,
    origin_prompt_fields: dict | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
    pipeline_params: dict | None = None,
    active_steps: list[str] | None = None,
    create_campaign_dir: bool = True,
) -> tuple[str, str]:
    """Mint session_id, write session state, claim active pointer; mutates session in place."""
    cycle_hash = cycle_id.removeprefix("cycle_")
    validate_path_component(cycle_hash)
    session_id = mint_session_id()

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "experiment_id": experiment_id,
            "dataset_name": session.dataset_name,
        },
        campaign_config=campaign_config.model_dump(),
        pipeline_params=pipeline_params or {},
        active_steps=list(active_steps or []),
    )
    state["origin_accuracy"] = origin_acc
    state["dataset_count"] = dataset_size
    state["origin_prompt_fields"] = origin_prompt_fields or {}

    sessions = session.store.sessions
    sessions.create(session_id, state)

    if create_campaign_dir:
        session.store.campaigns.create(
            session.backend_id,
            cycle_id,
            {
                "parent_session_id": session_id,
                "header": _build_index_header(session, dataset_size),
            },
        )

    session.session_id = session_id
    session.state.cycle_id = cycle_id

    save_active_pointer(session.store.tenant_id, session_id, cycle_id)
    logger.info("Auto-minted session %s + cycle %s", session_id, cycle_id)
    return session_id, cycle_id


def _open_cycle_ledger(session: Session, cycle_id: str) -> CycleEventLog | None:
    """Open per-cycle CycleEventLog; None when no store; idempotent (offsets cumulate)."""
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleEventLog

    if session.store is None:
        return None
    cycle_dir = CycleDir(session.store.campaigns.campaign_dir(cycle_id))
    return CycleEventLog.open(cycle_dir)
