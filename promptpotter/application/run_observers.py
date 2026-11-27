"""``build_run_observers`` holds every projection on one ledger's index; a fork re-anchors onto its own."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path

from promptpotter.application.initialization.session import Session
from promptpotter.application.optimizer_manifest import SelectedOptimizer
from promptpotter.application.pipeline_resolve import campaign_admits, defaults_drift
from promptpotter.application.run_phase_control import ProducerHold
from promptpotter.application.run_stop import run_stop_detail, run_stop_reason
from promptpotter.application.runner.output import ExportProjection
from promptpotter.application.scoring.closed_rounds import RoundFileProjection
from promptpotter.application.served_dashboard import spend_counts
from promptpotter.application.views.readout import ReadoutProjection, StatusFn
from promptpotter.domain.campaign import ceiling_meter
from promptpotter.domain.campaign_config import CampaignConfig
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.phases import (
    STOP_REASON_INFO,
    PauseCause,
    StopOutcome,
    StopReason,
    stop_reason_outcome,
)
from promptpotter.domain.records.error import ErrorRecord
from promptpotter.domain.records.run_records import (
    CycleShareRecord,
    RunPhaseRecord,
    RunWiringRecord,
)
from promptpotter.domain.spend import MONEY_AXES, Budget, StopSpend
from promptpotter.infrastructure.llm.spend_book import SpendBook
from promptpotter.infrastructure.llm.telemetry import append_record, billing_ledger, current_round
from promptpotter.infrastructure.projections.live_dashboard import DashboardCheckout
from promptpotter.infrastructure.run_scope import rescope, run_scope
from promptpotter.infrastructure.store.campaign_store.store import CycleIndexProjection
from promptpotter.infrastructure.store.read_model import LedgerIndex, Projecting
from promptpotter.infrastructure.store.stores import Stores


def declare_run_stop(
    session: Session,
    observers: RunObservers,
    stop_reason: StopReason,
    *,
    interrupted_by: PauseCause,
    detail: str = "",
) -> None:
    """Declared where the run ENDS, never at the checkpoint that raised it: a second site, a second
    record. *detail* is an ending's reason, where its raiser gave one."""
    limits = session.store.campaigns.run_limits(session.at)
    assert limits is not None, "a run stops on the wiring it declared"
    budget = limits.budget
    meter = ceiling_meter(session.purpose)
    # The cycle's share record, which its book reads live; none lands before admission's.
    held = session.store.campaigns.last(session.hop, CycleShareRecord)
    book = run_scope().book
    cycle, campaign = spend_counts(
        session.store, session.hop, meter, hold_id=None if book is None else book.hold_id
    )
    spend = StopSpend(
        spent=observers.dashboard.spend,
        meter=meter,
        cycle=cycle,
        campaign=campaign,
        cap=Budget(usd=budget.usd, tokens=budget.tokens),
        share=Budget() if held is None else held.allotment.only(MONEY_AXES),
    )
    if stop_reason_outcome(stop_reason) is not StopOutcome.PAUSED:
        stop = RunPhaseRecord.stop(stop_reason, detail=detail, spend=spend)
    else:
        cause, detail = session.state.control.take_pause() or (interrupted_by, "")
        stop = RunPhaseRecord.stop(stop_reason, cause=cause, detail=detail, spend=spend)
    session.store.declare_stop(session.hop, stop)


def end_run_on(exc: BaseException) -> tuple[StopReason, ErrorRecord | None, str]:
    """Call INSIDE the ``except``: the traceback is read off the live exception, dead by finalize.
    Answers the stop, its error record where it failed, and `run_stop_detail`."""
    reason, detail = run_stop_reason(exc), run_stop_detail(exc)
    info = STOP_REASON_INFO[reason]
    if info.outcome is not StopOutcome.FAILED:
        return reason, None, detail
    record = ErrorRecord.raised(
        exc,
        kind=type(exc).__name__,
        stop_reason=reason,
        traceback=traceback.format_exc() if info.has_traceback else None,
        round=current_round(),
    )
    append_record(record)
    return reason, record, detail


def declare_run_wiring(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    selected: SelectedOptimizer,
) -> None:
    """The one writer of ``RunWiringRecord``, on the cycle *session* is bound to: a mint's, a
    launch's or a fork's."""
    campaign = session.bound_campaign
    session.store.campaigns.append(
        session.hop,
        RunWiringRecord(
            round_cells_ceiling=selected.round_cells_ceiling(len(session.bank.samples)),
            display_metric=campaign_config.display_metric,
            elects_on=selected.elects_on,
            max_cells_in_flight=session.backend_client.connector.max_cells_in_flight,
            measured_unit=session.backend_client.connector.measured_unit,
            measurement_node=selected.plan.measurement,
            budget=campaign_config.optimization.budget,
            optimizer_limits=list(selected.limits),
            defaults_drift=defaults_drift(
                campaign,
                running=selected.treatment(
                    campaign_admits(campaign_config, session.bound_resolution.chain),
                    session.store.block_packs,
                ),
            ),
            backend_revision=session.backend_revision,
        ),
    )


@dataclass(frozen=True)
class RunObservers:
    store: Stores
    hop: CycleHop
    dashboard: DashboardCheckout
    readout: ReadoutProjection
    projecting: Projecting
    hold: ProducerHold

    def arm_spend_book(self, book: SpendBook) -> None:
        book.ledger = billing_ledger("the run's book")
        # A nested run (an L4 inner cell) admits each send against its own book AND its root's.
        if (mode := run_scope().instrument) is not None:
            # Still the spawner's book; the kind its pass files a cell as rides the mode.
            if (enclosing := run_scope().book) is None:
                raise RuntimeError("a nested run spends only under its enclosing run's book")
            book.enclosing = enclosing
            book.enclosed_as = mode.enclosed_as or book.enclosed_as
        rescope(book=book)

    def drain_all(self) -> None:
        """Drains only: the release is the run's `finally` and the fork handoff's."""
        self.projecting.close()
        # The SSE stream is no projection — it tails the on-disk ledger — so nothing drains it.
        book = run_scope().book
        if book is not None and book.ledger is not None and book.ledger.path == self.ledger_path:
            rescope(book=book.enclosing)

    @property
    def ledger_path(self) -> Path:
        return self.store.campaigns.ledger(self.hop)

    @property
    def cycle_dir(self) -> CycleDir:
        return CycleDir(self.store.campaigns.cycle_dir(self.hop))

    def release(self) -> None:
        """The run boundary, on every exit; its projections close undrained, as `drain_all` alone drains."""
        try:
            self.projecting.close(drain=False)
        finally:
            self.hold.release()


def build_run_observers(
    *,
    session: Session,
    readout_sink: StatusFn | None = None,
    forked_from: RunObservers | None = None,
) -> RunObservers:
    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.hop))
    # Before the ledger opens: from here a second launch of the cycle is refused, not interleaved.
    hold = ProducerHold(session.store, session.hop, scoring_pass=False)
    projecting = LedgerIndex.of(session.store.campaigns.ledger(session.hop)).projecting()
    try:
        return _bind_observers(
            cycle_dir,
            projecting,
            hold,
            session=session,
            forked_from=forked_from,
            readout_sink=readout_sink,
        )
    except BaseException:
        projecting.close(drain=False)
        hold.release()
        raise


def _bind_observers(
    cycle_dir: CycleDir,
    projecting: Projecting,
    hold: ProducerHold,
    *,
    session: Session,
    readout_sink: StatusFn | None,
    forked_from: RunObservers | None,
) -> RunObservers:
    readout = (
        ReadoutProjection(
            session.store,
            session.bound_resolution,
            session.dataset,
            measured_unit=session.backend_client.connector.measured_unit,
            sink=readout_sink,
        )
        if forked_from is None
        else forked_from.readout
    )
    dashboard = DashboardCheckout(cycle_dir)
    if dashboard.live.value() is None:
        raise RuntimeError(
            f"build_run_observers: the ledger of {session.hop} holds no RunWiringRecord"
        )
    dashboard.drain()
    readout.open_readout(session.hop)
    # Each joins at the line the index stands at, past what the checkout folded.
    dashboard = projecting.add(dashboard)
    readout = projecting.add(readout)
    projecting.add(CycleIndexProjection(session.store.campaigns, session.hop))
    projecting.add(RoundFileProjection(session.store, session.hop, session.state))
    projecting.add(ExportProjection(session.store, session.at, session.dataset))
    return RunObservers(
        store=session.store,
        hop=session.hop,
        dashboard=dashboard,
        readout=readout,
        projecting=projecting,
        hold=hold,
    )
