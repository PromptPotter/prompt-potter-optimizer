"""``build_run_observers`` holds every projection on one ledger's index; a fork re-anchors onto its own."""

from __future__ import annotations

import logging
import time
from contextvars import Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from promptpotter.application.initialization.session import Session
from promptpotter.application.jobs.coupon import bind_account
from promptpotter.application.optimizer_manifest import SelectedOptimizer, bound_optimizer
from promptpotter.application.pipeline_resolve import campaign_admits, defaults_drift
from promptpotter.application.run_callbacks import RunCallbacks
from promptpotter.application.runner.output import RunArtifacts
from promptpotter.application.scoring.closed_rounds import RoundFileProjection
from promptpotter.application.views.ingress import run_spend_view
from promptpotter.application.views.readout import ReadoutProjection, StatusFn
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.phases import (
    GateKind,
    PauseCause,
    RunPhase,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.run_records import (
    CycleShareRecord,
    RunPhaseRecord,
    RunWiringRecord,
)
from promptpotter.domain.spend import MONEY_AXES, Budget
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    bind_spend_book,
    bound_spend_book,
    reset_spend_book,
)
from promptpotter.infrastructure.producer_lock import (
    cycle_held_here,
    held,
    hold_cycle,
    release_cycle,
)
from promptpotter.infrastructure.projections.cycle_index import CycleIndexProjection
from promptpotter.infrastructure.projections.live_dashboard.projection import DashboardCheckout
from promptpotter.infrastructure.store.read_model import LedgerIndex, Projecting
from promptpotter.infrastructure.store.stores import Stores
from promptpotter.shared.clock import epoch_seconds
from promptpotter.shared.measurement_context import instrument_depth

if TYPE_CHECKING:
    from collections.abc import Sequence

    from promptpotter.domain.arms import Proposal
    from promptpotter.domain.campaign_config import CampaignConfig

logger = logging.getLogger(__name__)


def declare_running(ledger: CycleEventLog) -> None:
    ledger.append(RunPhaseRecord(run_phase=RunPhase.RUNNING))


def declare_gate(ledger: CycleEventLog, gate: GateKind, *, drafts: Sequence[Proposal] = ()) -> None:
    ledger.append(RunPhaseRecord(run_phase=RunPhase.GATE, gate=gate, drafts=list(drafts)))


def declare_run_stop(
    session: Session,
    observers: RunObservers,
    stop_reason: StopReason,
    *,
    interrupted_by: PauseCause,
) -> None:
    """Declared where the run ENDS, never at the checkpoint that raised it: a second site, a second record."""
    budget = session.store.campaigns.armed_run_limits(
        session.hop, observers.dashboard.live.state.run_limits
    ).budget
    meter = ceiling_meter(session.purpose)
    # The cycle's share record, which its book reads live; none lands before admission's.
    held = session.store.campaigns.last(session.hop, CycleShareRecord)
    spend = run_spend_view(
        observers.dashboard.live.spend_metered(meter),
        campaign_metered=session.store.campaigns.metered_spend(session.campaign_id, meter),
        usd_cap=budget.usd,
        token_cap=budget.tokens,
        share=Budget() if held is None else held.allotment.only(MONEY_AXES),
    )
    ledger = observers.callbacks.ledger
    if stop_reason_outcome(stop_reason) is not StopOutcome.PAUSED:
        stop = RunPhaseRecord.stop(stop_reason, spend=spend)
    else:
        cause, detail = session.state.control.take_pause() or (interrupted_by, "")
        stop = RunPhaseRecord.stop(stop_reason, cause=cause, detail=detail, spend=spend)
    ledger.append(stop)


def declare_run_wiring(
    session: Session,
    campaign_config: CampaignConfig,
    ledger: CycleEventLog,
    *,
    selected: SelectedOptimizer,
) -> None:
    """The one writer of ``RunWiringRecord``."""
    campaign = session.store.campaigns.load_campaign(session.hop.campaign_id)
    assert campaign is not None, "a run is wired on a campaign its session is bound to"
    ledger.append(
        RunWiringRecord(
            round_cells_ceiling=selected.round_cells_ceiling(len(session.bank.samples)),
            display_metric=campaign_config.display_metric,
            elects_on=selected.elects_on,
            max_cells_in_flight=session.backend_client.connector.max_cells_in_flight,
            measured_unit=session.backend_client.connector.measured_unit,
            measurement_node=selected.plan.measurement,
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
            backend_revision=session.backend_revision,
        )
    )


@dataclass(frozen=True)
class RunObservers:
    store: Stores
    hop: CycleHop
    callbacks: RunCallbacks
    dashboard: DashboardCheckout
    readout: ReadoutProjection
    projecting: Projecting
    _book_tokens: list[Token[SpendBook | None]] = field(default_factory=list)

    def arm_spend_book(self, book: SpendBook) -> None:
        ledger = self.callbacks.ledger
        self.projecting.add(book)
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

    def drain_all(self) -> None:
        try:
            self.projecting.close()
            # The SSE stream is no projection — it tails the on-disk ledger — so nothing drains it.
            if self._book_tokens:
                reset_spend_book(self._book_tokens.pop())
        finally:
            self.release()

    @property
    def cycle_dir(self) -> CycleDir:
        return CycleDir(self.store.campaigns.cycle_dir(self.hop))

    def release(self) -> None:
        """The run boundary, on every exit: while this process still holds the cycle, what its run
        never took is voided, a pause filed after the stop declared included; then the cycle goes."""
        try:
            if cycle_held_here(self.cycle_dir):
                self.store.void_inbox(self.hop)
        finally:
            _let_go(self.projecting, self.cycle_dir)


def _let_go(projecting: Projecting, cycle_dir: CycleDir) -> None:
    """Drops a run's projections undrained, since `drain_all` alone drains, then lets its cycle go."""
    projecting.close(drain=False)
    release_cycle(cycle_dir)


def build_run_observers(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    readout_sink: StatusFn | None = None,
    forked_from: RunObservers | None = None,
) -> RunObservers:
    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.hop))
    # A launch holds its cycle from its job's first live claim, else from here: a pause filed since
    # is its to take, a QUEUED one included.
    claim = session.store.campaigns.launch_claim(session.hop)
    held_since = (
        epoch_seconds(claim.timestamp)
        if claim is not None and held(Path(claim.claimant_lock))
        else None
    ) or time.time()
    # Before the ledger opens: from here a second launch of the cycle is refused, not interleaved.
    hold_cycle(cycle_dir)
    ledger = CycleEventLog.open(cycle_dir)
    projecting = LedgerIndex.of(ledger.path, ()).projecting()
    try:
        prior = session.store.campaigns.last(session.hop, RunPhaseRecord)
        observers = _bind_observers(
            cycle_dir,
            ledger,
            projecting,
            session=session,
            campaign_config=campaign_config,
            forked_from=forked_from,
            readout_sink=readout_sink,
        )
        # Any declaration after a `terminal` one retires that ending (`ledger_scan.py::_CycleFacts`).
        declare_running(observers.callbacks.ledger)
        bind_account(session.store)
        # A launch is a run boundary; the run a gate held resumes, what it was asked standing.
        if prior is None or prior.run_phase is not RunPhase.GATE:
            session.store.commands.void(
                session.store.subject(session.hop),
                f"the launch of {session.hop.cycle_id}",
                before=held_since,
            )
    except BaseException:
        _let_go(projecting, cycle_dir)
        raise
    session.state.observers = observers
    observers.callbacks.close_dead_bench()
    if forked_from is not None:
        # The line moved onto this cycle; the one it left is no longer this process's.
        forked_from.release()
        # A fork's config is already admitted; a first build waits for `prepare_run` to settle it.
        declare_run_wiring(
            session, campaign_config, observers.callbacks.ledger, selected=bound_optimizer()
        )
    return observers


def _bind_observers(
    cycle_dir: CycleDir,
    ledger: CycleEventLog,
    projecting: Projecting,
    *,
    session: Session,
    campaign_config: CampaignConfig,
    readout_sink: StatusFn | None,
    forked_from: RunObservers | None,
) -> RunObservers:
    readout = (
        ReadoutProjection.for_campaign(session, campaign_config, sink=readout_sink)
        if forked_from is None
        else forked_from.readout
    )
    dashboard = DashboardCheckout(cycle_dir)
    if dashboard.live.value() is None:
        raise RuntimeError(
            f"build_run_observers: the ledger of {session.hop} holds no RunWiringRecord"
        )
    dashboard.drain()
    readout.open_readout(cycle_dir)
    # Each joins at the line the index stands at, past what the checkout folded.
    dashboard = projecting.add(dashboard)
    readout = projecting.add(readout)
    projecting.add(CycleIndexProjection(cycle_dir))
    projecting.add(RoundFileProjection(session.store, session.hop, session.state))
    projecting.add(RunArtifacts(session, campaign_config))
    return RunObservers(
        store=session.store,
        hop=session.hop,
        callbacks=(
            RunCallbacks(ledger=ledger)
            if forked_from is None
            else RunCallbacks(ledger=ledger, view_context=forked_from.callbacks.view_context)
        ),
        dashboard=dashboard,
        readout=readout,
        projecting=projecting,
    )
