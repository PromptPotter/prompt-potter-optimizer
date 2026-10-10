"""``build_run_observers`` binds every projection to one ledger; a fork re-anchors onto its own."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from contextvars import Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_phase_control import declare_run_phase
from promptpotter.application.scoring.cells import RoundFileProjection
from promptpotter.application.views.readout import ReadoutProjection
from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.dashboard_rows import RunLimits
from promptpotter.domain.l4.proxies import PanelPrecision
from promptpotter.domain.phases import CampaignPhase, RunPhase
from promptpotter.domain.results import RoundResult, RunStanding, ScoredCandidate
from promptpotter.domain.run_records import (
    CandidateScoredRecord,
    CandidateStartedRecord,
    CycleRecord,
    ElectionRecord,
    FlightRecord,
    FlightWaiting,
    LedgerFit,
    PhaseRecord,
    RaceCatchUpRecord,
    RaceStandingRecord,
    RoundClosedRecord,
    RoundEnteredRecord,
    RoundStandingRecord,
    RunWiringRecord,
    SampleOrderRecord,
)
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
    set_current_round,
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
from promptpotter.shared.errors import graceful
from promptpotter.shared.measurement_context import instrument_depth

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.embedded_run import StatusFn
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizers.nodes import RaceSnapshot
    from promptpotter.application.scoring.query_loop import Flight
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.phase_views import PhaseView

logger = logging.getLogger(__name__)

__all__ = [
    "RunCallbacks",
    "RunObservers",
    "build_campaign_emitter",
    "build_run_observers",
    "declare_run_wiring",
    "run_limits_from",
]


def build_campaign_emitter(
    session: Session, *, resumed_from_round: int | None = None
) -> LiveDashboardProjection | None:
    return LiveDashboardProjection.for_session(
        session.hop, tenant_root=session.tenant_root, resumed_from_round=resumed_from_round
    )


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


def _round_fit(round_result: RoundResult) -> dict[str, LedgerFit]:
    """Keyed by LABEL, the arm's own key: an id names an individual several rounds can read."""
    return {
        cs.label: fit
        for cs in round_result.candidate_scores
        if (
            fit := LedgerFit(
                theta=cs.theta,
                theta_se=cs.theta_se,
                theta_caveat=cs.theta_caveat,
                vs_reference=cs.vs_reference,
            )
        )
        != LedgerFit()
    }


@dataclass
class RunCallbacks:
    ledger: CycleEventLog
    # What one phase view needs of an earlier one (`views/ingress.py`); a fork keeps its parent's.
    view_context: ViewContext = field(default_factory=ViewContext)
    _current_round: int = 0
    _round_token: Token[int | None] | None = field(default=None, init=False, repr=False)

    def _emit(self, record: CycleRecord) -> int | None:
        """``None`` where the append failed — ``next_offset`` then still names the previous record."""
        offset: int | None = None
        with graceful("ledger append failed"):
            offset = self.ledger.append(record)
        return offset

    def on_phase(
        self,
        phase: CampaignPhase | str,
        event: str,
        *,
        round: int | None = None,
        view: PhaseView | None = None,
    ) -> None:
        self._emit(PhaseRecord(phase=str(phase), event=event, round=round, view=view))

    def on_round_entered(self, round_num: int) -> None:
        self._emit(RoundEnteredRecord(round=round_num))

    def on_election(self, round_result: RoundResult) -> None:
        self._emit(
            ElectionRecord(
                round=round_result.round,
                fit=_round_fit(round_result),
                selected_labels=list(round_result.selected_labels),
                elects_on=round_result.elects_on,
                verdict_reason=round_result.verdict_reason,
            )
        )

    def on_round_close(self, round_result: RoundResult) -> int | None:
        return self._emit(RoundClosedRecord.of(round_result))

    def on_round_complete(
        self,
        round_result: RoundResult,
        standing: RunStanding,
        panel_precision: PanelPrecision | None,
    ) -> None:
        self.view_context.run_standing = standing
        self._emit(
            RoundStandingRecord(
                round=round_result.round,
                run_standing=standing,
                panel_precision=panel_precision,
                anchors=self.view_context.anchors(),
            )
        )

    def on_candidate_started(
        self,
        idx: int,
        total: int,
        changes_description: str,
        pipeline_overlay: dict[str, Any] | None,
        prompt_fields: dict[str, Any],
        resolved_pipeline_params: dict[str, Any] | None,
        block: Mapping[str, int] | None = None,
    ) -> None:
        self._emit(
            CandidateStartedRecord(
                round=self._current_round,
                candidate_idx=idx,
                candidate_total=total,
                changes_description=changes_description,
                pipeline_overlay=pipeline_overlay,
                prompt_fields=prompt_fields,
                resolved_pipeline_params=resolved_pipeline_params,
                block=None if block is None else dict(block),
            )
        )

    def announce_candidate(
        self,
        round_num: int,
        idx: int,
        total: int,
        *,
        opt_sp: OptSearchPoint,
        resolved_pipeline_params: dict[str, Any] | None,
        sample_order: Sequence[int],
        n_priors: int = 0,
        pipeline_overlay: dict[str, Any] | None = None,
        block: Mapping[str, int] | None = None,
    ) -> None:
        """Every site that scores an arm calls this, bar `rescore_parent`: the parent holds no slot."""
        self.on_candidate_started(
            idx,
            total,
            opt_sp.lineage.changes_description or "",
            pipeline_overlay,
            opt_sp.prompt_field_dict(),
            resolved_pipeline_params,
            block,
        )
        self.on_sample_order(
            round_num, idx, total, n_priors=n_priors, sample_order=list(sample_order)
        )

    def on_candidate_scored(self, idx: int, total: int, scores: ScoredCandidate) -> None:
        self._emit(
            CandidateScoredRecord(
                round=self._current_round,
                candidate_idx=idx,
                candidate_total=total,
                scores=scores,
                anchors=self.view_context.anchors(),
            )
        )

    def on_flight(self, flight: Flight) -> None:
        self._emit(
            FlightRecord(
                round=self._current_round,
                out=int(flight.out),
                allowed=int(flight.allowed),
                most=int(flight.most),
                affordable=None if flight.affordable is None else int(flight.affordable),
                cell_usd=None if flight.cell_usd is None else float(flight.cell_usd),
                waiting=None
                if flight.waiting is None
                else FlightWaiting(
                    sample_id=int(flight.waiting[0]), since=float(flight.waiting[1])
                ),
                backpressure=flight.backpressure,
            )
        )

    def on_race_standing(
        self, member: str, round_num: int, ci: int, ct: int, snapshot: RaceSnapshot
    ) -> None:
        self._emit(
            RaceStandingRecord(
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                member=member,
                current_id=str(snapshot.current_id),
                n_samples=int(snapshot.n_samples),
                p_best=float(snapshot.p_best),
                paired_breakdown=dict(snapshot.paired_breakdown),
                decision_grade=snapshot.decision_grade,
            )
        )

    def on_sample_order(
        self,
        round_num: int,
        ci: int,
        ct: int,
        *,
        n_priors: int,
        sample_order: list[int],
    ) -> None:
        """Not the heatmap's ``sample_order``: absolute difficulty there, relevance here."""
        self._emit(
            SampleOrderRecord(
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                n_priors=int(n_priors),
                sample_order=[int(sid) for sid in sample_order],
            )
        )

    def on_race_catch_up(
        self,
        member: str,
        round_num: int,
        ci: int,
        ct: int,
        sample_id: int,
        prior_ids: list[str],
    ) -> None:
        """No record where the cache already covered the priors."""
        if not prior_ids:
            return
        self._emit(
            RaceCatchUpRecord(
                round=round_num,
                candidate_idx=ci,
                candidate_total=ct,
                member=member,
                sample_id=int(sample_id),
                prior_ids=[str(p) for p in prior_ids],
            )
        )

    def set_round(self, round_num: int) -> None:
        """Only the first ``set`` carries a meaningful restore token; ``drain_all`` resets that one."""
        self._current_round = round_num
        token = set_current_round(round_num)
        if self._round_token is None:
            self._round_token = token


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
    dashboard = build_campaign_emitter(session, resumed_from_round=resumed_from_round)

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
