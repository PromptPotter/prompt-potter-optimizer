"""Single ingress for observer construction — one shape for CLI, notebook, future webapp.

``build_run_observers`` auto-mints session+cycle if missing, opens the
``CycleLedger``, builds and binds every projection + display in one pass.
Pass ``fork=ForkInfo(...)`` to rebuild observers around a forked cycle's
own ledger + audit dir while leaving the dashboard anchored at the family
root.

No two-phase init: callers receive a frozen ``RunObservers`` whose callbacks
already hold the bound ledger. ``run_optimization`` consumes this directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from promptpotter.application.baseline import (
    build_campaign_emitter,
    load_baseline_prompt,
)
from promptpotter.application.bootstrap import auto_mint_session
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.infrastructure.ledger import CycleLedger
from promptpotter.infrastructure.projections import (
    AuditTrailProjection,
    LiveDashboardProjection,
    PoBBStreamProjection,
)

if TYPE_CHECKING:
    from promptpotter.application.bootstrap import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.views.live import LiveDisplay

__all__ = ["ForkInfo", "RunObservers", "build_run_observers"]


@dataclass(frozen=True)
class RunObservers:
    """Frozen bundle: callbacks + projections + display, all bound to one ledger."""

    callbacks: RunCallbacks
    audit: AuditTrailProjection
    dashboard: LiveDashboardProjection
    pobb: PoBBStreamProjection
    display: LiveDisplay | None


def _ensure_session_minted(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    experiment_id: str | None,
    baseline_accuracy: float,
) -> None:
    """Auto-mint session+cycle from baseline OSP if missing (idempotent)."""
    from promptpotter.application.runner import build_baseline_cycle_id

    if session.session_id:
        return

    prompt_nodes = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    baseline_osp = load_baseline_prompt(
        session.experiment_extract or {},
        prompt_node_names=prompt_nodes,
        dataset_name=campaign_config.dataset_name,
    )
    auto_mint_session(
        session,
        campaign_config,
        cycle_id=build_baseline_cycle_id(baseline_osp, session.pipeline_schema, dataset),
        baseline_acc=baseline_accuracy,
        baseline_prompt_fields=baseline_osp.prompt_field_dict(),
        dataset_size=len(dataset),
        experiment_id=experiment_id,
    )


@dataclass(frozen=True)
class ForkInfo:
    """Forked-cycle wiring: parent cycle id + family-root dashboard."""

    parent_cycle_id: str
    parent_dashboard: LiveDashboardProjection


def build_run_observers(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    display: LiveDisplay | None = None,
    experiment_id: str | None = None,
    resumed_from_round: int | None = None,
    baseline_accuracy: float = 0.0,
    fork: ForkInfo | None = None,
) -> RunObservers:
    """Mint session if needed; open ledger; build + bind every observer.

    Pass ``fork=None`` for a fresh cycle (mints a new dashboard anchored at
    the family root). Pass ``fork=ForkInfo(...)`` when a fork-on-divergence
    has just minted a sibling cycle — the parent's dashboard is reattached
    to the fork's audit projection and the ledger inherits from the parent
    at its current offset. ``baseline_accuracy`` is a seed for
    ``dashboard.json``; the real value lands on the next ``INIT/exit``
    phase event after baseline runs.
    """
    if fork is None:
        _ensure_session_minted(
            session,
            campaign_config,
            dataset,
            experiment_id=experiment_id,
            baseline_accuracy=baseline_accuracy,
        )

    if session.state.cycle_id is None or session.store is None:
        raise RuntimeError("build_run_observers: session must have cycle_id and store")

    cycle_dir = CycleDir(session.store.campaigns.campaign_dir(session.state.cycle_id))
    audit = AuditTrailProjection.from_cycle_dir(cycle_dir)
    audit.rehydrate_sticky()
    session.state.audit_projection = audit
    pobb = PoBBStreamProjection.from_cycle_dir(cycle_dir)

    ledger = CycleLedger.open(cycle_dir)
    if fork is None:
        dashboard = build_campaign_emitter(
            session,
            campaign_config,
            baseline_accuracy=baseline_accuracy,
            resumed_from_round=resumed_from_round,
            recorder=audit,
        )
    else:
        dashboard = fork.parent_dashboard
        dashboard._recorder = audit
        dashboard.log_fork(
            old_cycle_id=fork.parent_cycle_id,
            new_cycle_id=session.state.cycle_id,
            from_round=resumed_from_round or 0,
        )
        parent_dir = CycleDir(session.store.campaigns.campaign_dir(fork.parent_cycle_id))
        fresh_parent = CycleLedger.open(parent_dir)
        ledger.inherit_from(fresh_parent, fresh_parent.next_offset)

    ledger.bind(dashboard)
    ledger.bind(audit)
    if display is not None:
        ledger.bind(display)
    ledger.bind(pobb)
    session.state.ledger = ledger

    callbacks = RunCallbacks(ledger=ledger)
    # Route every optimizer ``emit_token_usage`` call into the cycle
    # ledger via on_token_usage → TokenUsageRecord; the dashboard
    # projection sums these into spend.loop. The sink is process-global,
    # so a subsequent build_run_observers call cleanly replaces it.
    from promptpotter.infrastructure.llm import set_token_usage_sink

    set_token_usage_sink(callbacks.on_token_usage)

    return RunObservers(
        callbacks=callbacks,
        audit=audit,
        dashboard=dashboard,
        pobb=pobb,
        display=display,
    )
