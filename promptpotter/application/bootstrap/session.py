"""Session/Cycle identity + active-session-pointer claim. Owns ``Session`` + per-cycle bundles."""

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
from promptpotter.shared.identity import IdentityContext, default_identity

if TYPE_CHECKING:
    from promptpotter.application.intelligence.indexes import SampleIndex
    from promptpotter.domain.pipeline_schema import PipelineSchema
    from promptpotter.domain.validators import StopRule
    from promptpotter.infrastructure.ledger import CycleEventLog
    from promptpotter.infrastructure.projections import AuditTrailView
    from promptpotter.infrastructure.tracing import LangfuseLogger, ObservabilityBridge


logger = logging.getLogger(__name__)


@dataclass
class ScorerSetup:
    scorer: Scorer | None = None
    scorer_id: str = "none"
    scorer_formula: str | None = None
    round_scorer: RoundScorer | None = None
    scorer_round_formula: str | None = None
    scoring_set: list[Sample] = field(default_factory=list)
    sample_index: SampleIndex | None = None
    degradation_checks: list[StopRule] = field(default_factory=list)


@dataclass
class CycleSnapshot:
    """Per-cycle bundle — flips on fork."""

    cycle_id: str = ""
    tracing_campaign_id: str = ""
    resumed_from_round: int = 1
    obs: ObservabilityBridge | None = None
    audit_projection: AuditTrailView | None = None
    ledger: CycleEventLog | None = None
    # Forensic traceback for ``index.json::crash_traceback`` written by
    # ``mark_finished``. Operator-facing summary (kind + message) is owned by
    # the canonical ``ErrorRecord`` on the ledger; this field is the in-process
    # conduit between the runner's ``except`` block and ``_finalize_run`` only.
    crash_traceback: str | None = None


@dataclass
class Session:
    store: Stores
    backend_id: str
    experiment_id: str
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    samples: list[Sample] = field(default_factory=list)
    experiment_extract: dict[str, Any] = field(default_factory=dict)
    index_terms: list[str] = field(default_factory=list)
    identity: IdentityContext = field(default_factory=default_identity)
    dataset_name: str | None = None
    project_root: str = ""
    pipeline_params: dict[str, Any] = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None

    session_id: str = ""
    campaign_id: str = ""

    state: CycleSnapshot = field(default_factory=CycleSnapshot)
    scoring: ScorerSetup = field(default_factory=ScorerSetup)

    source: str = ""

    stop_check: Callable[[], bool] | None = None
    # `pause_check` follows the same shape: returns True while the operator
    # has requested a pause (`.runtime/pause.flag` present). The round loop
    # blocks at round boundaries until the flag clears. `stop_check` always
    # wins — stop-during-pause exits the wait loop cleanly.
    pause_check: Callable[[], bool] | None = None


def new_session_state(
    *,
    init_params: dict[str, Any],
    campaign_config: dict[str, Any],
    pipeline_params: dict[str, Any],
    active_steps: list[str],
) -> dict[str, Any]:
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
    origin_prompt_fields: dict[str, Any] | None = None,
    dataset_size: int = 0,
    experiment_id: str | None = None,
    pipeline_params: dict[str, Any] | None = None,
    active_steps: list[str] | None = None,
    label: str = "",
) -> tuple[str, str, str]:
    """Mint fresh campaign + session + root cycle; claim the active pointer."""
    from datetime import UTC, datetime

    from promptpotter.application.optimization.dispatch.llm_call import (
        combined_optimizer_prompt_hash,
    )
    from promptpotter.application.runner.identity import mint_campaign_id
    from promptpotter.domain.campaign import Campaign

    target_hash = cycle_id.removeprefix("cycle_")
    validate_path_component(target_hash)
    session_id = mint_session_id()
    now = datetime.now(UTC)
    dataset_name = session.dataset_name or campaign_config.dataset_name or ""
    optimizer_hash = combined_optimizer_prompt_hash()
    campaign_id = mint_campaign_id(dataset_name)
    root_cycle = cycle_id

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

    campaigns = session.store.campaigns
    campaigns.create_campaign(
        Campaign(
            campaign_id=campaign_id,
            dataset_name=dataset_name,
            label=label,
            created_at=now.isoformat(),
            status="active",
            root_cycle_id=root_cycle,
            root_content_hash=target_hash,
            optimizer_prompt_hash=optimizer_hash,
            backend_id=session.backend_id,
            owner_user_id=str(session.identity.user_id),
            lifecycle_status="active",
            lifecycle_changed_at=now.isoformat(),
            config=campaign_config.model_dump(mode="json"),
        )
    )

    campaigns.create(
        campaign_id,
        root_cycle,
        {
            "parent_session_id": session_id,
            "header": _build_index_header(session, dataset_size),
        },
    )

    session.session_id = session_id
    session.campaign_id = campaign_id
    session.state.cycle_id = root_cycle

    save_active_pointer(session.store.tenant_id, session_id, campaign_id, root_cycle)

    # Pre-seed dashboard.json so the webapp doesn't 404 in the mint→loop-start window.
    from promptpotter.application.origin import build_campaign_emitter
    from promptpotter.shared.errors import graceful

    with graceful("Pre-seeding dashboard.json failed"):
        build_campaign_emitter(session, campaign_config, origin_accuracy=origin_acc)

    logger.info(
        "Minted fresh campaign %s — session %s, cycle %s",
        campaign_id,
        session_id,
        root_cycle,
    )
    return session_id, campaign_id, root_cycle


def _open_cycle_ledger(session: Session, cycle_id: str) -> CycleEventLog | None:
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleEventLog

    if session.store is None:
        return None
    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.campaign_id, cycle_id))
    return CycleEventLog.open(cycle_dir)


__all__ = [
    "CycleSnapshot",
    "ScorerSetup",
    "Session",
    "auto_mint_session",
    "new_session_state",
]
