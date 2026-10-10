"""``build_run_observers`` binds every projection to one ledger; a fork re-anchors onto its own."""

from __future__ import annotations

import logging
from contextvars import Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.scoring.closed_rounds import RoundFileProjection
from promptpotter.application.views.readout import ReadoutProjection, StatusFn
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.phases import (
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import RunPhaseRecord, RunWiringRecord
from promptpotter.infrastructure.ledger import CycleEventLog, open_with_history
from promptpotter.infrastructure.llm.send_pacing import set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    bind_spend_book,
    reset_spend_book,
)
from promptpotter.infrastructure.llm.telemetry import (
    reset_current_round,
    reset_cycle_ledger,
    set_cycle_ledger,
)
from promptpotter.infrastructure.producer_lock import hold_cycle, release_cycle
from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
from promptpotter.infrastructure.projections.cycle_index import CycleIndexProjection
from promptpotter.infrastructure.projections.live_dashboard.projection import (
    LiveDashboardProjection,
)
from promptpotter.infrastructure.projections.racing_stream import RacingStreamProjection
from promptpotter.infrastructure.tracing.bridge import TracingProjection
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url
from promptpotter.shared.measurement_context import instrument_depth

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.domain.phase_views import RunSpendView

logger = logging.getLogger(__name__)

__all__ = [
    "RunObservers",
    "build_run_observers",
    "declare_run_phase",
    "declare_run_stop",
    "declare_run_wiring",
    "run_limits_from",
]


def declare_run_phase(session: Session, phase: Literal[RunPhase.RUNNING, RunPhase.GATE]) -> None:
    ledger = session.state.ledger
    if ledger is not None:
        ledger.append(RunPhaseRecord(run_phase=phase))


def declare_run_stop(
    session: Session,
    stop_reason: StopReason,
    *,
    interrupted_by: PauseCause,
    spend: RunSpendView | None = None,
) -> None:
    """Declared where the run ENDS, never at the checkpoint that raised it: a second site, a second record."""
    ledger = session.state.ledger
    if ledger is None:
        return
    if stop_reason_outcome(stop_reason) is not StopOutcome.PAUSED:
        ledger.append(RunPhaseRecord.stop(stop_reason, spend=spend))
        return
    cause, detail = session.control.take_pause() or (interrupted_by, "")
    ledger.append(RunPhaseRecord.stop(stop_reason, cause=cause, detail=detail, spend=spend))


def run_limits_from(config: CampaignConfig) -> RunLimits:
    opt = config.optimization
    return RunLimits(
        max_rounds=opt.max_rounds or None,
        ceiling=opt.ceiling,
        optimizer=list(select_optimizer(opt).limits),
    )


def declare_run_wiring(
    session: Session,
    campaign_config: CampaignConfig,
    ledger: CycleEventLog,
    *,
    tracing: TracingProjection | None,
) -> None:
    """The one writer of ``RunWiringRecord``; a mint passes no *tracing* — no launch has opened one."""
    selected = select_optimizer(campaign_config.optimization)
    ledger.append(
        RunWiringRecord(
            arms_per_round=selected.arms_per_round,
            sp_budget_round=selected.round_cells(len(session.samples)),
            display_metric=campaign_config.display_metric,
            elects_on=selected.elects_on,
            max_cells_in_flight=session.backend_client.max_cells_in_flight,
            measured_unit=session.backend_client.measured_unit,
            # Composed here: the Langfuse host is backend-only, so a browser cannot.
            langfuse_trace_url=None
            if tracing is None
            else langfuse_trace_url(tracing.langfuse_trace_id()),
            run_limits=run_limits_from(campaign_config),
        )
    )


@dataclass(frozen=True)
class RunObservers:
    cycle_dir: CycleDir
    callbacks: RunCallbacks
    audit: AuditTrailProjection
    dashboard: LiveDashboardProjection
    racing: RacingStreamProjection
    readout: ReadoutProjection
    tracing: TracingProjection
    _ledger_token: Token[CycleEventLog | None] | None = None
    _book_tokens: list[Token[SpendBook | None]] = field(default_factory=list)

    def arm_spend_book(self, book: SpendBook) -> None:
        ledger = self.callbacks.ledger
        ledger.bind(book)
        # A nested run (an L4 inner cell) admits nothing here: its sends stay on the ROOT's ceiling.
        if instrument_depth() == 0:
            book.ledger = ledger
            book.round_now = lambda: self.callbacks._current_round
            if self._book_tokens:
                reset_spend_book(self._book_tokens.pop())
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
        if self.callbacks._round_token is not None:
            reset_current_round(self.callbacks._round_token)
            self.callbacks._round_token = None


def build_run_observers(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    readout_sink: StatusFn | None = None,
    resumed_from_round: int | None = None,
    forked_from: RunObservers | None = None,
) -> RunObservers:
    if session.state.cycle_id is None:
        raise RuntimeError(
            "build_run_observers: session must already be minted via "
            "jobs.mint.prepare_fresh_cycle — it needs a cycle_id"
        )

    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.hop))
    # Before the ledger opens: from here a second launch of the cycle is refused, not interleaved.
    hold_cycle(cycle_dir)
    try:
        observers = _bind_observers(
            cycle_dir,
            session=session,
            campaign_config=campaign_config,
            readout_sink=readout_sink,
            resumed_from_round=resumed_from_round,
            forked_from=forked_from,
        )
    except BaseException:
        release_cycle(cycle_dir)
        raise
    if forked_from is not None:
        # The line moved onto this cycle; the one it left is no longer this process's.
        release_cycle(forked_from.cycle_dir)
        # A fork's config is already admitted; a first build waits for `_prepare_run` to settle it.
        declare_run_wiring(
            session, campaign_config, observers.callbacks.ledger, tracing=observers.tracing
        )
    return observers


def _bind_observers(
    cycle_dir: CycleDir,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    readout_sink: StatusFn | None,
    resumed_from_round: int | None,
    forked_from: RunObservers | None,
) -> RunObservers:
    audit = AuditTrailProjection.from_cycle_dir(cycle_dir)
    session.state.audit_projection = audit
    racing = RacingStreamProjection.from_cycle_dir(cycle_dir)

    ledger = open_with_history(cycle_dir)
    readout = (
        ReadoutProjection.for_campaign(session, campaign_config, sink=readout_sink)
        if forked_from is None
        else forked_from.readout
    )
    dashboard = LiveDashboardProjection.for_session(
        session.hop, tenant_root=session.tenant_root, resumed_from_round=resumed_from_round
    )

    if dashboard is None:
        raise RuntimeError(
            "build_run_observers: LiveDashboardProjection.for_session returned None — the session "
            f"address is incomplete (tenant_root={session.tenant_root!r}, hop={session.hop})"
        )

    ledger.bind(dashboard)
    ledger.bind(audit)
    readout.open_readout(cycle_dir)
    ledger.bind(readout)
    ledger.bind(racing)
    ledger.bind(CycleIndexProjection(cycle_dir))
    ledger.bind(RoundFileProjection(session, session.hop))
    tracing = (
        TracingProjection.for_cycle(session.tenant_root, session.hop, langfuse=session.langfuse)
        if forked_from is None
        else forked_from.tracing
    )
    ledger.bind(tracing)
    session.state.ledger = ledger
    # Any declaration after a `terminal` one retires that ending (`ledger_scan.py::_CycleFacts`).
    declare_run_phase(session, RunPhase.RUNNING)

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
