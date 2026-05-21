"""Session/Cycle identity + ``active_session.json`` claim.

Owns ``Session``, the per-cycle bundles it carries (``CycleState``,
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
class CycleState:
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
    # Set by ``run_round_loop`` when the loop ends with StopReason.CRASHED.
    # _finalize_run reads it and stamps it onto ``index.json::final.crash_traceback``
    # so the cause of the crash survives past terminal scrollback.
    crash_traceback: str | None = None


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
    # The campaign this session is working — stable across forks within
    # the campaign (only ``state.cycle_id`` flips when a fork is minted).
    campaign_id: str = ""

    # -- Per-cycle bundles ----------------------------------------------
    state: CycleState = field(default_factory=CycleState)
    scoring: ScoringContext = field(default_factory=ScoringContext)

    # -- Runtime config --------------------------------------------------
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
    label: str = "",
) -> tuple[str, str, str]:
    """Find-or-create the campaign, mint a session into it, claim the pointer.

    The ``campaign_id`` is the **declaration hash** — the target content
    hash (carried in *cycle_id* = ``cycle_{target_hash}``) folded with the
    optimizer meta-prompt hash. A re-run of ``new`` on an unchanged
    declaration (same target AND same optimizer prompts) resolves to the
    existing campaign:

    * **campaign exists** — mint a fresh session: a new ``session_id`` and a
      session-root cycle ``cycle_{target_hash}_s{N}`` (N = next free index).
      ``campaign.json``'s frozen snapshot is not rewritten; its status flips
      back to ``active``.
    * **new campaign** — write ``campaign.json`` (the declaration snapshot)
      and create the first session at the bare ``cycle_{target_hash}``.

    The root/session cycle ids stay *target*-based; only ``campaign_id``
    carries the optimizer half — so editing an optimizer meta-prompt mints
    a distinct campaign while two campaigns on one target still share their
    root cycle id (unique only within a campaign).

    Mutates *session* in place (``session_id``, ``campaign_id``,
    ``state.cycle_id``) and claims the 4-key active pointer. Returns
    ``(session_id, campaign_id, session_root_cycle_id)``.
    """
    from datetime import UTC, datetime

    from promptpotter.application.optimization.dispatch.llm_call import (
        combined_optimizer_prompt_hash,
    )
    from promptpotter.application.runner.identity import campaign_id_for, declaration_hash
    from promptpotter.domain.campaign import Campaign
    from promptpotter.infrastructure.store import session_cycle_id

    target_hash = cycle_id.removeprefix("cycle_")
    validate_path_component(target_hash)
    session_id = mint_session_id()
    now = datetime.now(UTC)
    dataset_name = session.dataset_name or campaign_config.dataset_name or ""
    optimizer_hash = combined_optimizer_prompt_hash()
    campaign_id = campaign_id_for(dataset_name, declaration_hash(target_hash, optimizer_hash))

    campaigns = session.store.campaigns
    existing_campaign = campaigns.load_campaign(campaign_id)
    if existing_campaign is None:
        next_index = 1
        root_cycle = cycle_id
    else:
        next_index = campaigns.next_session_index(campaign_id)
        root_cycle = session_cycle_id(cycle_id, next_index)

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

    if existing_campaign is None:
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
                config=campaign_config.model_dump(mode="json"),
            )
        )
    else:
        # A fresh `new` on this declaration reactivates the campaign — its
        # status reflects its most-recent session.
        campaigns.update_campaign(campaign_id, {"status": "active", "finished_at": ""})

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
    logger.info(
        "Auto-minted session %s (#%d) in campaign %s — cycle %s%s",
        session_id,
        next_index,
        campaign_id,
        root_cycle,
        "" if existing_campaign is None else " (existing campaign)",
    )
    return session_id, campaign_id, root_cycle


def _open_cycle_ledger(session: Session, cycle_id: str) -> CycleEventLog | None:
    """Open per-cycle CycleEventLog; None when no store; idempotent (offsets cumulate)."""
    from promptpotter.domain.cycle_paths import CycleDir
    from promptpotter.infrastructure.ledger import CycleEventLog

    if session.store is None:
        return None
    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.campaign_id, cycle_id))
    return CycleEventLog.open(cycle_dir)


__all__ = [
    "CycleState",
    "ScoringContext",
    "Session",
    "TenantContext",
    "auto_mint_session",
    "new_session_state",
]
