"""``build_run_observers`` binds every projection to one ledger; a fork re-anchors onto its own."""

from __future__ import annotations

import logging
from contextvars import Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, get_args

from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import SelectedOptimizer, bound_optimizer
from promptpotter.application.pipeline_resolve import campaign_admits, defaults_drift
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.scoring.closed_rounds import RoundFileProjection
from promptpotter.application.views.ingress import run_spend_view
from promptpotter.application.views.readout import ReadoutProjection, StatusFn
from promptpotter.domain.activity import ActivityFeed
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.phases import (
    GateKind,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import CycleRecord, RunPhaseRecord, RunWiringRecord
from promptpotter.infrastructure.ledger import CycleEventLog, open_with_history
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    bind_spend_book,
    bound_spend_book,
    reset_spend_book,
)
from promptpotter.infrastructure.llm.telemetry import reset_cycle_ledger, set_cycle_ledger
from promptpotter.infrastructure.producer_lock import hold_cycle, release_cycle
from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
from promptpotter.infrastructure.projections.base import Projection
from promptpotter.infrastructure.projections.cycle_index import CycleIndexProjection
from promptpotter.infrastructure.projections.live_dashboard.projection import DashboardCheckout
from promptpotter.infrastructure.projections.racing_stream import RacingStreamProjection
from promptpotter.infrastructure.runtime_flags import armed_run_limits
from promptpotter.infrastructure.tracing.bridge import TracingProjection
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url
from promptpotter.shared.measurement_context import instrument_depth

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.arms import Proposal
    from promptpotter.domain.campaign_config import CampaignConfig

logger = logging.getLogger(__name__)

#: Kinds no projection routes and no feed reads, each with the reader that does take it.
UNREPORTED_KINDS: frozenset[str] = frozenset(
    {
        "decision",  # replayed on resume (`resume_and_fork/replayers.py`)
        "account_event",  # the account trail's (`store/account_trail.py`)
        "trail_tombstone",  # the account trail's (`store/account_trail.py`)
        "spend_hold",  # the account spend book's (`store/account_spend.py`)
        "spend_tombstone",  # the account spend book's (`store/account_spend.py`)
        "health",  # the box health report's (`application/health.py`)
        "priced_key",  # scanned off the ledger (`ledger_scan.py`)
        "bank_partition",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "cycle_final",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "cycle_minted",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "cycle_share",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "cycle_superseded",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "fork_graded",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "intervention",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "launch_claim",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "launch_clock",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "launch_released",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "left_comparison",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "resolved_run",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "scoring_locked",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "spawned",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
        "standing_budget",  # a cycle fact scanned off the ledger (`ledger_scan.py`)
    }
)


def check_every_record_reported() -> None:
    """Every CycleRecord arm is routed by a projection, read by the feed, or listed unreported."""
    reported = set(ActivityFeed.records)
    pending = [Projection]
    while pending:
        projection = pending.pop()
        reported.update(projection.records)
        pending.extend(projection.__subclasses__())
    kinds = {
        arm.model_fields["record_type"].default: arm for arm in get_args(get_args(CycleRecord)[0])
    }
    dropped = sorted(
        k for k, arm in kinds.items() if arm not in reported and k not in UNREPORTED_KINDS
    )
    stale = sorted(k for k in UNREPORTED_KINDS if k not in kinds or kinds[k] in reported)
    if dropped or stale:
        raise RuntimeError(
            "A CycleRecord kind reaches no projection and no feed: route it, read it in "
            f"ActivityFeed, or list it in UNREPORTED_KINDS — dropped {dropped}, stale {stale}."
        )


def declare_running(session: Session) -> None:
    ledger = session.state.ledger
    if ledger is not None:
        ledger.append(RunPhaseRecord(run_phase=RunPhase.RUNNING))


def declare_gate(session: Session, gate: GateKind, *, drafts: Sequence[Proposal] = ()) -> None:
    ledger = session.state.ledger
    if ledger is not None:
        ledger.append(RunPhaseRecord(run_phase=RunPhase.GATE, gate=gate, drafts=list(drafts)))


def declare_run_stop(
    session: Session,
    observers: RunObservers,
    stop_reason: StopReason,
    *,
    interrupted_by: PauseCause,
) -> None:
    """Declared where the run ENDS, never at the checkpoint that raised it: a second site, a second record."""
    budget = armed_run_limits(
        Path(observers.cycle_dir), observers.dashboard.state.run_limits
    ).budget
    meter = ceiling_meter(session.purpose)
    spend = run_spend_view(
        observers.dashboard.spend_metered(meter),
        campaign_metered=session.store.campaigns.metered_spend(session.campaign_id, meter),
        usd_cap=budget.usd,
        token_cap=budget.tokens,
        share=session.state.control.book.allotment,
    )
    ledger = observers.callbacks.ledger
    if stop_reason_outcome(stop_reason) is not StopOutcome.PAUSED:
        ledger.append(RunPhaseRecord.stop(stop_reason, spend=spend))
        return
    cause, detail = session.state.control.take_pause() or (interrupted_by, "")
    ledger.append(RunPhaseRecord.stop(stop_reason, cause=cause, detail=detail, spend=spend))


def declare_run_wiring(
    session: Session,
    campaign_config: CampaignConfig,
    ledger: CycleEventLog,
    *,
    selected: SelectedOptimizer,
    tracing: TracingProjection | None,
) -> None:
    """The one writer of ``RunWiringRecord``; a mint passes no *tracing* — no launch has opened one."""
    campaign = session.store.campaigns.load_campaign(session.hop.campaign_id)
    assert campaign is not None, "a run is wired on a campaign its session is bound to"
    ledger.append(
        RunWiringRecord(
            round_cells_ceiling=selected.round_cells_ceiling(len(session.bank.samples)),
            display_metric=campaign_config.display_metric,
            elects_on=selected.elects_on,
            max_cells_in_flight=session.backend_client.max_cells_in_flight,
            measured_unit=session.backend_client.measured_unit,
            # Composed here: the Langfuse host is backend-only, so a browser cannot.
            langfuse_trace_url=None
            if tracing is None
            else langfuse_trace_url(tracing.langfuse_trace_id()),
            run_limits=RunLimits(
                budget=campaign_config.optimization.budget, optimizer=list(selected.limits)
            ),
            defaults_drift=defaults_drift(
                campaign,
                running=selected.treatment(
                    campaign_admits(campaign_config, session.pipeline_schema),
                    session.store.block_packs,
                ),
            ),
        )
    )


@dataclass(frozen=True)
class RunObservers:
    cycle_dir: CycleDir
    callbacks: RunCallbacks
    audit: AuditTrailProjection
    dashboard: DashboardCheckout
    racing: RacingStreamProjection
    readout: ReadoutProjection
    tracing: TracingProjection
    _ledger_token: Token[CycleEventLog | None] | None = None
    _book_tokens: list[Token[SpendBook | None]] = field(default_factory=list)

    def arm_spend_book(self, book: SpendBook) -> None:
        ledger = self.callbacks.ledger
        ledger.bind(book)
        book.ledger = ledger
        if self._book_tokens:
            reset_spend_book(self._book_tokens.pop())
        # A nested run (an L4 inner cell) admits each send against its own book AND its root's.
        if instrument_depth() > 0:
            book.enclosing = bound_spend_book()
            if book.enclosing is None:
                raise RuntimeError("a nested run spends only under its enclosing run's book")
        self._book_tokens.append(bind_spend_book(book))
        unreported = book.take_up(ledger)
        if unreported.sends:
            logger.warning(
                "%d paid send(s) on this run ended unpriced; the ceiling holds up to $%.4f "
                "for them, and no surface counts it as spent.",
                unreported.sends,
                unreported.usd,
            )

    def drain_all(self, *, interrupted: bool = False) -> None:
        try:
            self._drain(interrupted=interrupted)
        finally:
            release_cycle(self.cycle_dir)

    def _drain(self, *, interrupted: bool) -> None:
        self.audit.drain(interrupted=interrupted)
        self.dashboard.drain()
        self.racing.drain()
        self.tracing.drain()
        # The SSE stream is no subscriber — it tails the on-disk ledger — so nothing drains here.
        if self._ledger_token is not None:
            reset_cycle_ledger(self._ledger_token)
        if self._book_tokens:
            reset_spend_book(self._book_tokens.pop())


def build_run_observers(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    readout_sink: StatusFn | None = None,
    forked_from: RunObservers | None = None,
) -> RunObservers:
    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.hop))
    # Before the ledger opens: from here a second launch of the cycle is refused, not interleaved.
    hold_cycle(cycle_dir)
    try:
        observers = _bind_observers(
            cycle_dir,
            session=session,
            campaign_config=campaign_config,
            readout_sink=readout_sink,
            forked_from=forked_from,
        )
    except BaseException:
        release_cycle(cycle_dir)
        raise
    session.state.locked_cycle = cycle_dir
    if forked_from is not None:
        # The line moved onto this cycle; the one it left is no longer this process's.
        release_cycle(forked_from.cycle_dir)
        # A fork's config is already admitted; a first build waits for `prepare_run` to settle it.
        declare_run_wiring(
            session,
            campaign_config,
            observers.callbacks.ledger,
            selected=bound_optimizer(),
            tracing=observers.tracing,
        )
    return observers


def _bind_observers(
    cycle_dir: CycleDir,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    readout_sink: StatusFn | None,
    forked_from: RunObservers | None,
) -> RunObservers:
    audit = AuditTrailProjection.from_cycle_dir(cycle_dir)
    session.state.audit_projection = audit
    racing = RacingStreamProjection.from_cycle_dir(cycle_dir)
    readout = (
        ReadoutProjection.for_campaign(session, campaign_config, sink=readout_sink)
        if forked_from is None
        else forked_from.readout
    )
    dashboard = DashboardCheckout.over(cycle_dir)
    if dashboard is None:
        raise RuntimeError(
            f"build_run_observers: the ledger of {session.hop} holds no RunWiringRecord"
        )
    # Opened after the checkout folded, so its catch-up never feeds the checkout a line twice.
    ledger = open_with_history(cycle_dir)

    ledger.bind(dashboard)
    ledger.bind(audit)
    readout.open_readout(cycle_dir)
    ledger.bind(readout)
    ledger.bind(racing)
    ledger.bind(CycleIndexProjection(cycle_dir))
    ledger.bind(RoundFileProjection(session.store, session.hop, session.state))
    tracing = (
        TracingProjection.for_cycle(session.tenant_root, session.hop, langfuse=session.langfuse)
        if forked_from is None
        else forked_from.tracing
    )
    ledger.bind(tracing)
    session.state.ledger = ledger
    # Any declaration after a `terminal` one retires that ending (`ledger_scan.py::_CycleFacts`).
    declare_running(session)

    callbacks = (
        RunCallbacks(ledger=ledger)
        if forked_from is None
        else RunCallbacks(ledger=ledger, view_context=forked_from.callbacks.view_context)
    )
    ledger_token = set_cycle_ledger(ledger)
    # No token to reset: the run's task owns its context, and an inner cell rebinds the same account.
    set_rate_tenant(str(session.store.identity.tenant_id))

    return RunObservers(
        cycle_dir=cycle_dir,
        callbacks=callbacks,
        audit=audit,
        dashboard=dashboard,
        racing=racing,
        readout=readout,
        tracing=tracing,
        _ledger_token=ledger_token,
    )
