"""Run observers + callbacks — the single ingress for CLI/notebook/webapp. ``build_run_observers``
wires audit + dashboard + racing stream + readout to one ledger, re-anchoring a fork."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from contextvars import Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.optimizer_manifest import select_optimizer
from promptpotter.application.run_phase_control import declare_run_phase
from promptpotter.application.views.ingress import from_phase_event
from promptpotter.application.views.readout import ReadoutProjection
from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.dashboard_rows import RunStanding
from promptpotter.domain.phases import RunPhase
from promptpotter.domain.results import RoundResult
from promptpotter.domain.run_records import (
    CycleRecord,
    ElectionRecord,
    LedgerAbility,
    LedgerFit,
    PhaseRecord,
    SnapshotRecord,
)
from promptpotter.domain.scoring import QueryMeasurement, ledger_sample_view
from promptpotter.infrastructure.ledger import CycleEventLog, open_with_history
from promptpotter.infrastructure.llm.rate_limit import set_rate_tenant
from promptpotter.infrastructure.llm.spend_book import (
    SpendBook,
    bind_spend_book,
    reset_spend_book,
    unreported_on,
)
from promptpotter.infrastructure.llm.telemetry import (
    reset_current_round,
    reset_cycle_ledger,
    set_current_round,
    set_cycle_ledger,
)
from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
from promptpotter.infrastructure.projections.live_dashboard.projection import (
    LiveDashboardProjection,
)
from promptpotter.infrastructure.projections.live_dashboard.state import RunLimits
from promptpotter.infrastructure.projections.racing_stream import RacingStreamProjection
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url
from promptpotter.shared.errors import graceful
from promptpotter.shared.instrument import NO_ROUND_SLOT, instrument_depth

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.embedded_run import StatusFn
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimizers.nodes import RaceSnapshot
    from promptpotter.application.scoring.query_loop import Flight
    from promptpotter.domain.opt_search_point import OptSearchPoint
    from promptpotter.domain.phases import PhaseEvent

logger = logging.getLogger(__name__)

__all__ = [
    "RunCallbacks",
    "RunObservers",
    "build_campaign_emitter",
    "build_run_observers",
    "run_limits_from",
]


# What ``current_query_payload`` shows the operator (``LiveStateCard``). The cap belongs at the
# writer, where the record is decided — never sliced off a full query by the reader.
QUERY_PREVIEW_CHARS = 120


def build_campaign_emitter(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    resumed_from_round: int | None = None,
    langfuse_trace_url: str | None = None,
) -> LiveDashboardProjection | None:
    """Live dashboard projection from session + config, seeded from the cycle's own history — a
    fork's walks its parent's ledger up to the cut. ``None`` back when the session carries no
    cycle to write into, which the return type states."""
    selected = select_optimizer(campaign_config.optimization)
    return LiveDashboardProjection.for_session(
        session.hop,
        tenant_root=session.tenant_root,
        session_id=session.session_id,
        arms_per_round=selected.pacing.arms_per_round,
        sp_budget_round=selected.round_cells(len(session.samples)),
        display_metric=campaign_config.display_metric,
        langfuse_trace_url=langfuse_trace_url,
        resumed_from_round=resumed_from_round,
        # The connector's own declarations, read at wiring — origin scoring runs before any
        # phase event, so anything that waits for one is absent exactly when round 0 needs it.
        max_cells_in_flight=session.backend_client.max_cells_in_flight,
        measured_unit=session.backend_client.measured_unit,
        stamps_theta=selected.stamps_theta,
    )


def run_limits_from(config: CampaignConfig) -> RunLimits:
    """The declared ceilings, read off ONE config — the same object ``_build_budget_gate`` takes
    its arms from, so the number on screen is the number that halts. Stamped by ``_prepare_run``
    once the held ceiling is set on it: earlier is the unadmitted config, and the ledger's own
    INIT record lands after the entire origin has scored."""
    opt = config.optimization
    return RunLimits(
        max_rounds=opt.max_rounds or None,
        spend_budget_usd=opt.spend_budget_usd,
        token_budget=opt.token_budget,
        optimizer=list(select_optimizer(opt).pacing.limits),
    )


def _round_abilities(round_result: RoundResult) -> dict[str, LedgerAbility]:
    """Each row's θ, **keyed by LABEL** (``C{round}.{idx}``) — a resume re-mints ids, so an id join
    drops it. The crown is not here; it rides ``ElectionRecord``."""
    return {
        cs.label: ability
        for cs in round_result.candidate_scores
        if (
            ability := LedgerAbility(
                theta=cs.theta, theta_se=cs.theta_se, theta_caveat=cs.theta_caveat
            )
        )
        != LedgerAbility()
    }


def _round_fit(round_result: RoundResult) -> dict[str, LedgerFit]:
    """Each arm's ELECTION stamps — θ and the matched-parent floor it was judged against — under
    the same LABEL key and for the same reason as :func:`_round_abilities`.

    An untouched arm is dropped rather than served as a row of nulls: on a cold ruler no candidate
    carries θ at all, and an arm below the coverage floor never reaches the fit."""
    return {
        cs.label: fit.model_copy(update={"reference_id": cs.reference_id})
        for cs in round_result.candidate_scores
        if (
            fit := LedgerFit(
                theta=cs.theta,
                theta_se=cs.theta_se,
                theta_caveat=cs.theta_caveat,
                reference_accuracy=cs.reference_accuracy,
                reference_composite=cs.reference_composite,
                reference_lift=cs.reference_lift,
                reference_lift_ci_lo=cs.reference_lift_ci_lo,
                reference_lift_ci_hi=cs.reference_lift_ci_hi,
            )
        )
        != LedgerFit()
    }


@dataclass
class RunCallbacks:
    """Single ingress: callbacks → typed ``CycleRecord`` → ``CycleEventLog.append``, ledger bound at
    construction. ``_round_token`` resets the ``_CURRENT_ROUND`` ContextVar ``emit_token_usage`` reads."""

    ledger: CycleEventLog
    _phase_ctx: ViewContext = field(default_factory=ViewContext)
    _current_round: int = 0
    _round_token: Token[int | None] | None = field(default=None, init=False, repr=False)

    def _emit(self, record: CycleRecord) -> int | None:
        """The offset the record landed at, ``None`` where the append failed. Returned rather
        than left for a caller to infer off ``next_offset``: a counter that never moved reads
        as the previous record, so an address would be stamped for an event nobody wrote."""
        offset: int | None = None
        with graceful("ledger append failed"):
            offset = self.ledger.append(record)
        return offset

    def on_phase(self, event: PhaseEvent) -> None:
        view = from_phase_event(event, self._phase_ctx)
        # ``data`` rides the in-memory-only field, so the on-disk dump stays clean JSON
        # (the SSE tail re-reads it verbatim) without a key filter deciding which of the
        # caller's handles serialize. The typed ``view`` is the record; it is capped, and
        # every disk re-reader takes phase state from it.
        self._emit(
            PhaseRecord(
                phase=str(event.phase),
                event=str(event.event),
                round=event.round,
                data=event.data,
                payload={"view": view},
            )
        )

    def on_election(self, round_result: RoundResult) -> None:
        """What the election produced, at the moment it produced it — from ``execute_round`` once
        the panel gate has let the round stand, and from ``emit_origin_round`` for round 0, which
        adopts ``C0``. Round 0 differs in the VALUE it carries, never in the record it writes.

        The crown and the per-arm fit travel together because they are stamped together, before
        the round closes."""
        self._emit(
            ElectionRecord(
                round=round_result.round,
                fit=_round_fit(round_result),
                live_round_result=round_result,
                selected_labels=list(round_result.selected_labels),
                stamps_theta=round_result.stamps_theta,
            )
        )

    def on_round_close(self, round_result: RoundResult) -> int | None:
        """The CLOSE — what the round knows and no candidate could: the frontier it advanced and
        the ability fit behind it. Every term here is RE-READ on each close, which is what lets
        round 0's second one (``round.py::close_round``, once the ruler warms) deliver a θ its own
        close could not have had. The crown is deliberately absent: it never moves, so it lands
        once, at ``on_election``.

        Returns the offset this close landed at — the round document's address on the ledger."""
        return self._emit(
            PhaseRecord(
                phase="round",
                event="complete",
                round=round_result.round,
                payload={
                    "accuracy": round_result.accuracy,
                    "composite_fitness": round_result.composite_fitness,
                    "improved": round_result.improved,
                    # Banked beside `improved` because the life bank needs both to replay a
                    # round identically on resume (`EscalationFSM.fold`): `improved` says which
                    # way to move the bank, this says whether to move it at all.
                    "electable_count": round_result.electable_count,
                    # The third of the same set: whether the round could tell its arms apart at
                    # all. The stall counter gates on it, so a resume that could not read it here
                    # would rebuild a different patience than the live run spent.
                    "separable": round_result.separable,
                    "label": round_result.label,
                    # WHOLE, not the θ alone: round 0's second close restamps this reading onto
                    # the served summary, and a θ landing under the cold scale it replaced is
                    # exactly the split `AbilityReading` exists to make unrepresentable.
                    "ability": (
                        round_result.ability.model_dump()
                        if round_result.ability is not None
                        else None
                    ),
                    "abilities": _round_abilities(round_result),
                },
            )
        )

    def on_round_complete(self, round_result: RoundResult, standing: RunStanding) -> None:
        # ``event="display"`` keeps ``EscalationFSM.fold`` reading only the lean ``event="complete"`` audit emit.
        # The full ``RoundResult`` rides ``live_round_result`` (in-memory-only) for
        # the live subscribers; disk persists only the three scalars the SSE→webapp
        # chat reads — the fat arrays are already in round_NNNN.json + dashboard.json.
        # The standing persists here, the one record the lineage tree reads it back from.
        self._phase_ctx.run_standing = standing
        self._emit(
            PhaseRecord(
                phase="round",
                event="display",
                round=round_result.round,
                live_round_result=round_result,
                payload={
                    "round_result": {
                        "round": round_result.round,
                        "accuracy": round_result.accuracy,
                        "composite_fitness": round_result.composite_fitness,
                    },
                    "run_standing": standing.model_dump(),
                    "phase_ctx": self._phase_ctx.ledger_anchors(),
                },
            )
        )

    def _snapshot(
        self,
        event: str,
        ci: int,
        ct: int,
        payload: dict[str, Any],
        *,
        round_num: int | None = None,
        sample_idx: int | None = None,
        sample_total: int | None = None,
    ) -> None:
        self._emit(
            SnapshotRecord(
                event=event,
                round=self._current_round if round_num is None else round_num,
                candidate_idx=ci,
                candidate_total=ct,
                sample_idx=sample_idx,
                sample_total=sample_total,
                payload=payload,
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
        # `prompt_fields` + `pipeline_overlay` are the candidate's evolved searchpoint
        # (the seed-able half), surfaced live so the steer panel can fork from a
        # still-in-flight candidate without the round file — the in-flight peer
        # of round_NNNN.json::candidate_scores. `resolved_pipeline_params` is the
        # config-only resolved config the OBSERVE view reads (the in-flight peer
        # of ScoredCandidate.resolved_pipeline_params).
        self._snapshot(
            "candidate_started",
            idx,
            total,
            {
                "changes_description": changes_description,
                "pipeline_overlay": pipeline_overlay,
                "prompt_fields": prompt_fields,
                "resolved_pipeline_params": resolved_pipeline_params,
                "block": None if block is None else dict(block),
            },
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
        """Everything a reader needs BEFORE an arm walks: WHAT it is, and WHICH cells it will walk.

        **The origin is a candidate of round 0 like any other**, so ONE call carries the pair.
        Composed by hand per site, a caller emits one of the two and its round silently loses its
        walk axis or its searchpoint; a third site that scores an arm calls this or goes dark the
        same way.

        ``block`` is the turn's place in a block race — block ``n`` of ``of``, ``size`` cells,
        ``racing`` arms live — so an arm announces once per block it walks.

        `rescore_parent` is deliberately NOT one: the parent occupies no slot in the round's
        population (`NO_ROUND_SLOT`), so it announces no candidate while still ticking samples."""
        self.on_candidate_started(
            idx,
            total,
            opt_sp.lineage.changes_description or "",
            pipeline_overlay,
            opt_sp.prompt_field_dict(),
            resolved_pipeline_params,
            block,
        )
        self.on_sample_order_preview(
            round_num, idx, total, n_priors=n_priors, sample_order=list(sample_order)
        )

    def on_candidate_scored(self, idx: int, total: int, scores: dict[str, Any]) -> None:
        self._snapshot(
            "candidate_scored",
            idx,
            total,
            {"scores": scores, "phase_ctx": self._phase_ctx.ledger_anchors()},
        )

    def on_sample_started(
        self,
        ci: int,
        ct: int,
        query_text: str,
        qi: int,
        qt: int,
        sample_id: int,
        sample_lookahead: int = 1,
        stop_horizon: int | None = None,
    ) -> None:
        """``sample_lookahead`` is what the walk held in flight as it launched this one — what the loop
        did, not what the operator's flag asked for. ``stop_horizon`` is the fewest rows at which a
        stop rule could still cut as it launched, ``None`` where none could inside the window.
        ``query_preview`` is capped HERE: its sole reader showed the first 120 chars, so the rest was
        never a record of anything — the whole query is a dataset fact, on disk at
        ``datasets/{slug}/`` and in every measurement row."""
        self._snapshot(
            "sample_started",
            ci,
            ct,
            {
                "query_preview": query_text[:QUERY_PREVIEW_CHARS],
                "sample_id": int(sample_id),
                "sample_lookahead": int(sample_lookahead),
                "stop_horizon": stop_horizon,
            },
            sample_idx=qi,
            sample_total=qt,
        )

    def on_flight(self, flight: Flight) -> None:
        """The scoring phase's calls in flight, what its stop rules allow, what the spend ceiling
        affords, the most it could hold, and the call a decision waits on — the whole round's, so
        it names no candidate (``scoring/query_loop.py::FlightGauge``)."""
        waiting = (
            None
            if flight.waiting is None
            else {"sample_id": int(flight.waiting[0]), "since": float(flight.waiting[1])}
        )
        self._snapshot(
            "flight",
            NO_ROUND_SLOT,
            0,
            {
                "out": int(flight.out),
                "allowed": int(flight.allowed),
                "most": int(flight.most),
                # What the SPEND ceiling admits beside them, which no other reading here implies.
                "affordable": None if flight.affordable is None else int(flight.affordable),
                "cell_usd": None if flight.cell_usd is None else float(flight.cell_usd),
                "waiting": waiting,
                "backpressure": (
                    None if flight.backpressure is None else flight.backpressure.model_dump()
                ),
            },
        )

    def on_sample_scored(self, ci: int, ct: int, result: dict[str, Any], qi: int, qt: int) -> None:
        self._snapshot(
            "sample_scored",
            ci,
            ct,
            {"result": ledger_sample_view(cast("QueryMeasurement", result))},
            sample_idx=qi,
            sample_total=qt,
        )

    def on_race_standing(
        self, member: str, round_num: int, ci: int, ct: int, snapshot: RaceSnapshot
    ) -> None:
        """Per-sample race standing from the eliminator ``member`` — archive-only, not
        divergence-gated. ``snapshot`` stays TYPED: an ``Any`` on a seam that only destructures
        breaks silently on the next field removal."""
        self._snapshot(
            "race_standing",
            ci,
            ct,
            {
                "member": member,
                "current_id": str(snapshot.current_id),
                "n_samples": int(snapshot.n_samples),
                "p_best": float(snapshot.p_best),
                "paired_breakdown": dict(snapshot.paired_breakdown),
                "decision_grade": snapshot.decision_grade,
            },
            round_num=round_num,
            sample_idx=int(snapshot.n_samples) - 1,
        )

    def on_sample_order_preview(
        self,
        round_num: int,
        ci: int,
        ct: int,
        *,
        n_priors: int,
        sample_order: list[int],
    ) -> None:
        """The round's shared deterministic scoring order (``build_round_order``), emitted at candidate
        start. Distinct from the heatmap's ``sample_order`` — absolute difficulty there, relevance here."""
        self._snapshot(
            "sample_order_preview",
            ci,
            ct,
            {
                "n_priors": int(n_priors),
                "sample_order": [int(sid) for sid in sample_order],
            },
            round_num=round_num,
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
        """The race's priors caught up on the just-measured sample; absence ⇒ cache covered it."""
        if not prior_ids:
            return
        self._snapshot(
            "race_catch_up",
            ci,
            ct,
            {
                "member": member,
                "sample_id": int(sample_id),
                "prior_ids": [str(p) for p in prior_ids],
            },
            round_num=round_num,
        )

    def set_round(self, round_num: int) -> None:
        """Update the round marker stamped onto every subsequent ``TokenUsageRecord``. Always a fresh
        ``set``: only the first one carries a meaningful restore token, and ``drain_all`` holds that one."""
        self._current_round = round_num
        token = set_current_round(round_num)
        if self._round_token is None:
            self._round_token = token


@dataclass(frozen=True)
class RunObservers:
    """Frozen bundle: callbacks + projections on one ledger. ``_ledger_token`` resets the
    ``_CYCLE_LEDGER`` ContextVar, so no background task inherits a stale ledger from the last cycle."""

    callbacks: RunCallbacks
    audit: AuditTrailProjection
    dashboard: LiveDashboardProjection
    racing: RacingStreamProjection
    readout: ReadoutProjection
    _ledger_token: Token[CycleEventLog | None] | None = None
    # The armed spend book's binding, one at a time — see `arm_spend_book`.
    _book_tokens: list[Token[SpendBook | None]] = field(default_factory=list)

    def arm_spend_book(self, book: SpendBook) -> None:
        """Count this run's ledger into ``book`` and admit every send the run makes against it,
        replacing a book armed before — the ceilings are re-armed once the origin is scored. Every
        send the ledger left unreported — a killed run's calls out included — is held from here,
        before this run sends anything, and never counted as spent.

        A run nested inside another (an L4 inner cell) admits nothing against its own book: its
        sends stay on the ROOT's, the one ceiling every level of the recursion spends under, and
        its book only reads its own ledger for its own surfaces."""
        ledger = self.callbacks.ledger
        ledger.bind(book)
        if instrument_depth() == 0:
            book.ledger = ledger
            book.round_now = lambda: self.callbacks._current_round
            if self._book_tokens:
                reset_spend_book(self._book_tokens.pop())
            self._book_tokens.append(bind_spend_book(book))
        unreported = unreported_on(ledger)
        book.usd_unreported, book.tokens_unreported = unreported.usd, unreported.tokens
        if unreported.sends:
            logger.warning(
                "%d paid send(s) on this run ended without a bill; the ceiling holds up to $%.4f "
                "for them, and no surface counts it as spent.",
                unreported.sends,
                unreported.usd,
            )

    def drain_all(self, *, interrupted: bool = False) -> None:
        """``drain()`` every projection + reset both emission ContextVars. Called on EVERY stop reason, so
        the audit cache reflects the ledger even on interrupt; ``interrupted`` marks the partial
        round file of a stop that left a round open."""
        self.audit.drain(interrupted=interrupted)
        self.dashboard.drain()
        self.racing.drain()
        # The SSE stream isn't a subscriber — it tails the on-disk ledger
        # (``CycleLedgerTail``), so there's nothing to drain/deregister here;
        # open HTTP tails idle on heartbeats once the run stops appending.
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
    """Open the ledger with its history bound; build + bind every observer. A fork mid-launch passes
    the observers it ``forked_from`` and keeps their readout and view context: INIT:enter fires once."""
    if session.state.cycle_id is None:
        raise RuntimeError(
            "build_run_observers: session must already be minted via "
            "jobs.mint.prepare_fresh_cycle — it needs a cycle_id"
        )

    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.hop))
    audit = AuditTrailProjection.from_cycle_dir(cycle_dir)
    session.state.audit_projection = audit
    racing = RacingStreamProjection.from_cycle_dir(cycle_dir)

    ledger = open_with_history(cycle_dir)
    # A set-once identity stamp (like session_id), not a tracing-stream read — fan-out-only
    # stays intact. None when Langfuse is disabled. Keyed by `tracing_campaign_id`, the stable
    # root-cycle key the trace was stored under: a fork reassigns `cycle_id` but emits into
    # the same root trace, so the live `cycle_id` misses the lookup.
    obs = session.state.obs
    trace_url = (
        langfuse_trace_url(obs.get_langfuse_trace_id(session.state.tracing_campaign_id))
        if obs
        else None
    )
    readout = (
        ReadoutProjection.for_campaign(session, campaign_config, sink=readout_sink)
        if forked_from is None
        else forked_from.readout
    )
    dashboard = build_campaign_emitter(
        session,
        campaign_config,
        resumed_from_round=resumed_from_round,
        langfuse_trace_url=trace_url,
    )

    # `for_session` answers `None` on an incomplete address — the two halves the guard above
    # already covers, plus `tenant_root` / `session_id`. Binding that None to the ledger would
    # raise here anyway; saying which field is empty costs one line and names the cause.
    if dashboard is None:
        raise RuntimeError(
            "build_run_observers: LiveDashboardProjection.for_session returned None — the session "
            f"address is incomplete (tenant_root={session.tenant_root!r}, "
            f"session_id={session.session_id!r}, hop={session.hop})"
        )

    # Profile A — the outbound SSE highway is served by tailing the on-disk
    # ledger (``CycleLedgerTail``), cross-process, so there's nothing to register
    # here. The runner just appends; any reader (the API server, the CLI, a
    # future MCP client) tails ``.runtime/ledger.jsonl`` directly.
    ledger.bind(dashboard)
    ledger.bind(audit)
    readout.open_readout(cycle_dir)
    ledger.bind(readout)
    ledger.bind(racing)
    session.state.ledger = ledger
    # Every launch says so on the ledger: a resume of a paused cycle is otherwise a run the
    # ledger still calls paused, and the ledger is where the run phase is read.
    declare_run_phase(session, RunPhase.RUNNING)

    callbacks = (
        RunCallbacks(ledger=ledger)
        if forked_from is None
        else RunCallbacks(ledger=ledger, _phase_ctx=forked_from.callbacks._phase_ctx)
    )
    # Bind the ledger into the per-asyncio-task ContextVar so emit_token_usage
    # finds it without a process-global sink. Token rides on RunObservers so
    # drain_all can restore the prior context on teardown.
    ledger_token = set_cycle_ledger(ledger)
    # ...and whose share of the shared provider window this run draws on. Same context, same
    # reason: the limiter is one object per provider serving every concurrent campaign. No token
    # to reset — the run's task owns its context and dies with it, and an L4 inner cell rebinds
    # the same account rather than earning a second share.
    set_rate_tenant(str(session.store.identity.tenant_id))

    return RunObservers(
        callbacks=callbacks,
        audit=audit,
        dashboard=dashboard,
        racing=racing,
        readout=readout,
        _ledger_token=ledger_token,
    )
