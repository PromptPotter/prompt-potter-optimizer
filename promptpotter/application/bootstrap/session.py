"""Session/Cycle identity + active-session-pointer claim. Owns ``Session`` + per-cycle bundles."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from promptpotter.domain.phases import StopReason
from promptpotter.domain.sample import Sample
from promptpotter.domain.scoring import RoundScorer, Scorer
from promptpotter.infrastructure.backend import BackendClient
from promptpotter.infrastructure.store import (
    Stores,
    mint_session_id,
    save_active_pointer,
)
from promptpotter.infrastructure.store.io import validate_path_component
from promptpotter.infrastructure.store.layout import CycleLayout
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
    backend_client: BackendClient
    pipeline_schema: PipelineSchema | None
    samples: list[Sample] = field(default_factory=list)
    index_terms: list[str] = field(default_factory=list)
    identity: IdentityContext = field(default_factory=default_identity)
    dataset_name: str | None = None
    # Resolved tenant-first at bootstrap (`resolve_dataset_config_dir`): tenant
    # uploads at `projects/{tenant}/datasets/{slug}/`, else repo `datasets/{name}/`.
    # The single resolution seam — every dataset-file loader reads this rather than
    # recomputing a repo path from the bare name.
    dataset_config_dir: Path | None = None
    project_root: str = ""
    pipeline_params: dict[str, Any] = field(default_factory=dict)
    langfuse: LangfuseLogger | None = None

    session_id: str = ""
    campaign_id: str = ""

    state: CycleSnapshot = field(default_factory=CycleSnapshot)
    scoring: ScorerSetup = field(default_factory=ScorerSetup)

    source: str = ""
    # This cycle was babysat — an operator directly edited an engine-owned/locked
    # value (ADR-0005). Read from the cycle index at bootstrap; forces every run
    # this cycle scores to grade C (excluded from digest / reuse / L4).
    human_intervened: bool = False

    def llm_node_name(self) -> str:
        """The dataset's prompt-bearing LLM node — the override target for a per-cell
        seed / model pin.

        Derived from the pipeline schema, never a literal. The L4 inner runner used to
        hardcode ``"llm_only"``: an inner dataset naming its node anything else had the CRN
        seed written under a key that did not exist, so the seed never landed and the outer
        paired (variant − origin) diff silently lost its variance cancellation. Raises rather
        than guessing — a missing schema means the caller ran before bootstrap.
        """
        names = self.pipeline_schema.prompt_node_names() if self.pipeline_schema else []
        if not names:
            raise ValueError(
                f"dataset {self.dataset_name!r} declares no prompt-bearing node — "
                "cannot target a per-cell seed / model override"
            )
        return names[0]

    # `pause_check` returns True while the operator has requested a pause
    # (`.runtime/pause.flag` present — the single operator-interrupt flag). The
    # loop checkpoints poll it and, when set, declare PAUSED and exit cleanly:
    # the worker ends, the cycle stays resumable. Bound at the runner seam and
    # also fed to `set_abort_check` so a pause breaks a long rate-limit wait.
    pause_check: Callable[[], bool] | None = None
    # `skip_check` returns True while a one-shot `.runtime/skip.flag` is present
    # (operator early-abort of the searchpoint scoring now). Unlike pause it
    # does NOT end the cycle — the per-sample checkpoint accepts the partial and
    # continues. `skip_consume` removes the flag the instant it fires so exactly
    # one searchpoint is cut, not the whole round.
    skip_check: Callable[[], bool] | None = None
    skip_consume: Callable[[], None] | None = None
    # `budget_tripped` returns the `StopReason` once a spend/token ceiling is met, else None.
    # Bound at the runner seam to the SAME `BudgetGate.tripped` the round loop consults — one
    # object, so the two cadences can't disagree and a mid-flight ceiling change moves both.
    # The round-boundary check alone let a whole round of scoring run past the ceiling; for an
    # L4 outer round that is `n_candidates x n_samples` inner CAMPAIGNS of overshoot.
    budget_tripped: Callable[[], StopReason | None] | None = None


def new_session_state(
    *,
    init_params: dict[str, Any],
    pipeline_params: dict[str, Any],
    active_steps: list[str],
) -> dict[str, Any]:
    return {
        "phase": "init",
        "init_params": init_params,
        "pipeline_params": pipeline_params,
        "active_steps": active_steps,
        "origin_prompt_fields": {},
        "dataset_count": 0,
        "origin_accuracy": 0.0,
        "task_context": None,
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
    pipeline_params: dict[str, Any] | None = None,
    active_steps: list[str] | None = None,
    label: str = "",
) -> tuple[str, str, str]:
    """Mint fresh campaign + session + root cycle; claim the active pointer."""
    from datetime import UTC, datetime

    from promptpotter.application.config import freeze_campaign_config
    from promptpotter.application.optimization.dispatch.llm_call import (
        combined_optimizer_prompt_hash,
    )
    from promptpotter.application.runner.identity import mint_campaign_id
    from promptpotter.domain.campaign import Campaign

    target_hash = cycle_id.removeprefix("cycle_")
    validate_path_component(target_hash)
    session_id = mint_session_id()
    now = datetime.now(UTC)
    # Precedence matches configure_and_apply_pipeline: the persisted campaign
    # snapshot is authoritative, the live session is the fallback.
    dataset_name = campaign_config.dataset_name or session.dataset_name or ""
    optimizer_hash = combined_optimizer_prompt_hash()
    campaign_id = mint_campaign_id(dataset_name)
    root_cycle = cycle_id

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "dataset_name": session.dataset_name,
        },
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
            root_cycle_id=root_cycle,
            root_content_hash=target_hash,
            optimizer_prompt_hash=optimizer_hash,
            backend_id=session.backend_id,
            owner_user_id=str(session.identity.user_id),
            lifecycle_status="active",
            lifecycle_changed_at=now.isoformat(),
            config=freeze_campaign_config(campaign_config),
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

    save_active_pointer(
        session.store.tenant_id,
        session_id,
        campaign_id,
        root_cycle,
        projects_root=session.store.projects_root,
    )

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


def mint_checkin_skeleton(stores: Stores, *, slug: str) -> tuple[str, str, str]:
    """Mint a real disk-backed campaign in the ``checkin`` lifecycle — transition (a).

    The first ingest action lands here: write a provisional ``campaign.json``
    (``lifecycle_status="checkin"``, empty ``root_content_hash`` / ``config`` — the
    origin isn't authored yet), a skeleton cycle ``index.json``, a placeholder
    session, and drop ``.runtime/checkin.flag`` so ``derive_run_phase`` reads the
    cycle as ``CHECKIN``. No Session, no backend, no quota, no machine slot — a
    check-in is resumable progress, not a run. **It does NOT claim the active
    pointer**: the pointer is "the cycle the dashboard follows", and a not-yet-run
    check-in following it would snap a following workspace off the chat mid-drop
    (bouncing the operator out of the authoring flow). ``finalize_checkin_to_active``
    claims the pointer when it flips this to ``active`` at Start. The draft
    working-state + sample bank are written separately by the caller through
    :class:`CheckinDraftStore`. Returns ``(session_id, campaign_id, cycle_id)``."""
    from datetime import UTC, datetime

    from promptpotter.application.runner.identity import mint_campaign_id, mint_checkin_cycle_id
    from promptpotter.config.settings import APP_VERSION
    from promptpotter.domain.campaign import Campaign

    now = datetime.now(UTC).isoformat()
    campaign_id = mint_campaign_id(slug)
    cycle_id = mint_checkin_cycle_id()
    session_id = mint_session_id()

    stores.campaigns.create_campaign(
        Campaign(
            campaign_id=campaign_id,
            dataset_name=slug,
            created_at=now,
            root_cycle_id=cycle_id,
            root_content_hash="",
            backend_id="",
            owner_user_id=str(stores.identity.user_id),
            lifecycle_status="checkin",
            lifecycle_changed_at=now,
            config={},
        )
    )
    stores.campaigns.create(
        campaign_id,
        cycle_id,
        {
            "parent_session_id": session_id,
            "header": {
                "tool": "promptpotter",
                "version": APP_VERSION,
                "dataset_name": slug,
                "backend_id": "",
            },
        },
    )
    # Placeholder session row so re-open + the sidebar's session count resolve a real
    # session between skeleton and Start; finalize overwrites it with the run state.
    stores.sessions.create(session_id, {"phase": "checkin", "dataset_name": slug})

    checkin_flag = CycleLayout(stores.campaigns.cycle_dir(campaign_id, cycle_id)).checkin_flag
    checkin_flag.parent.mkdir(parents=True, exist_ok=True)
    checkin_flag.write_text("", encoding="utf-8")

    logger.info(
        "Minted check-in campaign %s — session %s, cycle %s", campaign_id, session_id, cycle_id
    )
    return session_id, campaign_id, cycle_id


def finalize_checkin_to_active(
    session: Session,
    campaign_config: Any,
    *,
    campaign_id: str,
    cycle_id: str,
    session_id: str,
    cycle_plan: Any,
    dataset_size: int,
) -> None:
    """Flip a ``checkin`` campaign to ``active`` against its EXISTING ids — transition (b).

    The deferred half of the mint: the origin is now resolved (``cycle_plan``), so
    write the real ``config`` + ``root_content_hash`` onto the provisional
    ``campaign.json`` and flip ``lifecycle_status`` ``checkin`` → ``active``,
    overwrite the placeholder session with the full run state, fill the cycle index
    header, pre-seed ``dashboard.json``, and clear ``.runtime/checkin.flag``. The
    cycle id stays the provisional ``cycle_chk_*`` (option 2b — drift reads
    ``root_content_hash``, not the parsed id). Unlike ``auto_mint_session`` this
    mints nothing new; the caller binds the session + detaches the run."""
    from datetime import UTC, datetime

    from promptpotter.application.config import freeze_campaign_config
    from promptpotter.application.optimization.dispatch.llm_call import (
        combined_optimizer_prompt_hash,
    )

    now = datetime.now(UTC).isoformat()
    target_hash = cycle_plan.cycle_id.removeprefix("cycle_")
    plan_origin_fields = cycle_plan.origin.prompt_field_dict()

    state = new_session_state(
        init_params={
            "backend_url": session.backend_client.base_url,
            "backend_id": session.backend_id,
            "dataset_name": session.dataset_name,
        },
        pipeline_params=cycle_plan.pipeline_params,
        active_steps=list(cycle_plan.pipeline_params.get("steps", [])),
    )
    state["dataset_count"] = dataset_size
    state["origin_prompt_fields"] = plan_origin_fields
    session.store.sessions.create(session_id, state)

    session.store.campaigns.update_campaign(
        campaign_id,
        {
            "root_content_hash": target_hash,
            "optimizer_prompt_hash": combined_optimizer_prompt_hash(),
            "backend_id": session.backend_id,
            "lifecycle_status": "active",
            "lifecycle_changed_at": now,
            "config": freeze_campaign_config(campaign_config),
        },
    )
    session.store.campaigns.create(
        campaign_id,
        cycle_id,
        {
            "parent_session_id": session_id,
            "header": _build_index_header(session, dataset_size),
        },
    )

    session.session_id = session_id
    session.campaign_id = campaign_id
    session.state.cycle_id = cycle_id

    # Claim the active pointer now (not at skeleton mint) — Start is when the cycle
    # becomes the running one the dashboard follows. A following workspace snaps to
    # it here, so the operator lands on the live run instead of being bounced
    # mid-authoring (the skeleton deliberately left the pointer alone).
    # projects_root threads the store's own root so a sandboxed inner cycle (L4)
    # stamps its OWN workspace pointer, never the outer tenant's — otherwise the
    # inner mint clobbers the outer's active_session.json and the webapp (which
    # reads the default root) follows a pointer to a campaign that lives under
    # `.inner/…` and 404s.
    save_active_pointer(
        session.store.tenant_id,
        session_id,
        campaign_id,
        cycle_id,
        projects_root=session.store.projects_root,
    )

    cycle_dir = session.store.campaigns.cycle_dir(campaign_id, cycle_id)
    CycleLayout(cycle_dir).checkin_flag.unlink(missing_ok=True)

    from promptpotter.application.origin import build_campaign_emitter
    from promptpotter.shared.errors import graceful

    with graceful("Pre-seeding dashboard.json failed"):
        build_campaign_emitter(session, campaign_config, origin_accuracy=0.0)

    logger.info(
        "Check-in campaign %s started — session %s, cycle %s", campaign_id, session_id, cycle_id
    )


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
    "finalize_checkin_to_active",
    "mint_checkin_skeleton",
    "new_session_state",
]
