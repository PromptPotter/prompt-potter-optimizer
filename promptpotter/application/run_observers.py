"""Run observers + callbacks — the single ingress for CLI/notebook/webapp. ``build_run_observers``
wires audit + dashboard + PoBB stream + optional ``LiveDisplay`` to one ledger, re-anchoring a fork."""

from __future__ import annotations

from contextvars import Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.views.ingress import from_phase_event
from promptpotter.application.views.view_models import ViewContext
from promptpotter.domain.cycle_paths import CycleDir, CycleHop
from promptpotter.domain.results import RoundResult, is_round_winner
from promptpotter.domain.run_records import (
    CycleRecord,
    ElectionRecord,
    LedgerAbility,
    PhaseRecord,
    SnapshotRecord,
)
from promptpotter.domain.scoring import QueryMeasurement, ledger_sample_view
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm.telemetry import (
    reset_current_round,
    reset_cycle_ledger,
    set_current_round,
    set_cycle_ledger,
)
from promptpotter.infrastructure.projections.audit_trail import AuditTrailView
from promptpotter.infrastructure.projections.live_dashboard.view import LiveDashboardView
from promptpotter.infrastructure.projections.pobb_stream import PoBBStreamView
from promptpotter.infrastructure.tracing.langfuse_client import langfuse_trace_url
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.campaign_config import CampaignConfig
    from promptpotter.application.initialization.session import Session
    from promptpotter.application.optimization.pobb.checks import PoBBSnapshot
    from promptpotter.domain.phases import PhaseEvent
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.views.live.display import LiveDisplay

__all__ = [
    "QUERY_PREVIEW_CHARS",
    "ForkInfo",
    "RunCallbacks",
    "RunObservers",
    "build_campaign_emitter",
    "build_run_observers",
]


# What ``current_query_payload`` shows the operator (``LiveStateCard``). The reader used to
# slice this off a full query it was handed; the cap belongs at the writer, where the record
# is decided.
QUERY_PREVIEW_CHARS = 120


def build_campaign_emitter(
    session: Session,
    campaign_config: CampaignConfig,
    *,
    origin_accuracy: float,
    resumed_from_round: int | None = None,
    recorder: AuditTrailView | None = None,
    seed_from_cycle_id: str | None = None,
    langfuse_trace_url: str | None = None,
) -> LiveDashboardView | None:
    """Live dashboard projection from session + config. ``seed_from_cycle_id`` names the parent
    cycle to seed prior trajectory from; ``None`` seeds from the cycle's own dir. ``None`` back
    when the session carries no cycle to write into — the type says so now; it used to say ``Any``."""
    opt = campaign_config.optimization
    return LiveDashboardView.for_session(
        session.hop,
        tenant_root=session.tenant_root,
        session_id=session.session_id,
        l1_patience=opt.l1_patience,
        n_variants=opt.n_variants,
        sp_budget_ttest=campaign_config.sp_budget_ttest,
        headline_metric=campaign_config.headline_metric,
        langfuse_trace_url=langfuse_trace_url,
        resumed_from_round=resumed_from_round,
        recorder=recorder,
        seed_from_cycle_id=seed_from_cycle_id,
        # The connector's own declarations, read at wiring — origin scoring runs before any
        # phase event, so anything that waits for one is absent exactly when round 0 needs it.
        max_cells_in_flight=session.backend_client.max_cells_in_flight,
        concurrency_arming=session.backend_client.concurrency_arming,
        measured_unit=session.backend_client.measured_unit,
    )


def _round_abilities(round_result: RoundResult) -> dict[str, LedgerAbility]:
    """Each row's θ, **keyed by LABEL** (``C{round}.{idx}``) — a resume re-mints ids, so an id join
    drops it. The crown is not here; it rides ``ElectionRecord``."""
    return {
        cs.label: ability
        for cs in round_result.candidate_scores
        if (ability := LedgerAbility(theta=cs.theta, theta_se=cs.theta_se)) != LedgerAbility()
    }


@dataclass
class RunCallbacks:
    """Single ingress: callbacks → typed ``CycleRecord`` → ``CycleEventLog.append``, ledger bound at
    construction. ``_round_token`` resets the ``_CURRENT_ROUND`` ContextVar ``emit_token_usage`` reads."""

    ledger: CycleEventLog
    _phase_ctx: ViewContext = field(default_factory=ViewContext)
    _current_round: int = 0
    _round_token: Token[int | None] | None = field(default=None, init=False, repr=False)

    def _emit(self, record: CycleRecord) -> None:
        with graceful("ledger append failed"):
            self.ledger.append(record)

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
        """The crown, at the moment the election produced it — from ``execute_round`` once the
        panel gate has let the round stand, and from ``emit_origin_round`` for round 0, which
        adopts ``C0``. Round 0 differs in the VALUE it carries, never in the record it writes."""
        self._emit(
            ElectionRecord(
                round=round_result.round,
                # `winner_id` is non-empty on a HELD round too — it names the retained
                # incumbent, which is no candidate of THIS round, so the match fails and the
                # crown is empty. The emptiness is in the match, never in the id.
                winner_label=next(
                    (
                        cs.label
                        for cs in round_result.candidate_scores
                        if is_round_winner(cs.candidate_id, round_result.winner_id)
                    ),
                    "",
                ),
            )
        )

    def on_round_close(self, round_result: RoundResult) -> None:
        """The CLOSE — what the round knows and no candidate could: the frontier it advanced and
        the ability fit behind it. Every term here is RE-READ on each close, which is what lets
        round 0's second one (``runner/loop.py``, once the ruler warms) deliver a θ its own close
        could not have had. The crown is deliberately absent: it never moves, so it lands once, at
        ``on_election``."""
        self._emit(
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

    def on_round_complete(
        self, round_result: RoundResult, l1_stall_count: int, hearts: int | None = None
    ) -> None:
        # ``event="display"`` keeps ``EscalationFSM.fold`` reading only the lean ``event="complete"`` audit emit.
        # The full ``RoundResult`` rides ``live_round_result`` (in-memory-only) for
        # the live subscribers; disk persists only the three scalars the SSE→webapp
        # chat reads — the fat arrays are already in round_NNNN.json + dashboard.json.
        # ``hearts`` = the banked-lives count (``None`` when lives mode is off) — the
        # high-level ♥ readout, a peer of the stall counter on the same channel.
        self._phase_ctx.l1_stall_count = l1_stall_count
        self._phase_ctx.hearts = hearts
        self._emit(
            PhaseRecord(
                phase="round",
                event="display",
                round=round_result.round,
                live_round_result=round_result,
                payload={
                    "round_result": {
                        "round": round_result.round,
                        "accuracy": float(round_result.accuracy),
                        "composite_fitness": float(round_result.composite_fitness),
                    },
                    "l1_stall_count": l1_stall_count,
                    "hearts": hearts,
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
        pp_override: dict[str, Any] | None,
        prompt_fields: dict[str, Any],
        resolved_pipeline_params: dict[str, Any] | None,
    ) -> None:
        # `prompt_fields` + `pp_override` are the candidate's evolved searchpoint
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
                "pp_override": pp_override,
                "prompt_fields": prompt_fields,
                "resolved_pipeline_params": resolved_pipeline_params,
            },
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
    ) -> None:
        """``sample_lookahead`` is what the walk held in flight as it launched this one — what the loop
        did, not what the operator's flag asked for. ``query_preview`` is capped HERE: its sole reader
        showed the first 120 chars, so the rest was never a record of anything — the whole query is a
        dataset fact, on disk at ``datasets/{slug}/`` and in every measurement row."""
        self._snapshot(
            "sample_started",
            ci,
            ct,
            {
                "query_preview": query_text[:QUERY_PREVIEW_CHARS],
                "sample_id": int(sample_id),
                "sample_lookahead": int(sample_lookahead),
            },
            sample_idx=qi,
            sample_total=qt,
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

    def on_p_best_update(self, round_num: int, ci: int, ct: int, snapshot: PoBBSnapshot) -> None:
        """Per-sample PoBB snapshot — archive-only, not divergence-gated. ``snapshot`` stays TYPED: an
        ``Any`` on a seam that only destructures breaks silently on the next field removal."""
        self._snapshot(
            "p_best_update",
            ci,
            ct,
            {
                "current_id": str(snapshot.current_id),
                "n_samples": int(snapshot.n_samples),
                "p_best": float(snapshot.p_best),
                "paired_breakdown": dict(snapshot.paired_breakdown),
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

    def on_pobb_backfill(
        self,
        round_num: int,
        ci: int,
        ct: int,
        sample_id: int,
        prior_ids: list[str],
    ) -> None:
        """Paired-PoBB priors caught up on the just-measured sample; absence ⇒ cache covered it."""
        if not prior_ids:
            return
        self._snapshot(
            "pobb_backfill",
            ci,
            ct,
            {"sample_id": int(sample_id), "prior_ids": [str(p) for p in prior_ids]},
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
    """Frozen bundle: callbacks + projections + display on one ledger. ``_ledger_token`` resets the
    ``_CYCLE_LEDGER`` ContextVar, so no background task inherits a stale ledger from the last cycle."""

    callbacks: RunCallbacks
    audit: AuditTrailView
    dashboard: LiveDashboardView
    pobb: PoBBStreamView
    display: LiveDisplay | None
    _ledger_token: Token[CycleEventLog | None] | None = None

    def drain_all(self) -> None:
        """``drain()`` every projection + reset both emission ContextVars. Called on EVERY stop reason, so
        the audit cache reflects the ledger even on interrupt."""
        self.audit.drain()
        self.dashboard.drain()
        self.pobb.drain()
        # The SSE stream isn't a subscriber — it tails the on-disk ledger
        # (``CycleLedgerTail``), so there's nothing to drain/deregister here;
        # open HTTP tails idle on heartbeats once the run stops appending.
        if self._ledger_token is not None:
            reset_cycle_ledger(self._ledger_token)
        if self.callbacks._round_token is not None:
            reset_current_round(self.callbacks._round_token)
            self.callbacks._round_token = None


@dataclass(frozen=True)
class ForkInfo:
    """Forked-cycle wiring: the parent cycle to seed from — the fork gets its own ``dashboard.json``."""

    parent_cycle_id: str


def build_run_observers(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    display: LiveDisplay | None = None,
    resumed_from_round: int | None = None,
    origin_accuracy: float = 0.0,
    fork: ForkInfo | None = None,
) -> RunObservers:
    """Open the ledger; build + bind every observer. A fork inherits the parent's ledger at its current
    offset and seeds a fresh dashboard from the parent's drained file. An unminted session is a bug."""
    if session.state.cycle_id is None or session.store is None:
        raise RuntimeError(
            "build_run_observers: session must already be minted via "
            "jobs.mint.prepare_fresh_cycle — it needs both cycle_id and store"
        )

    cycle_dir = CycleDir(session.store.campaigns.cycle_dir(session.hop))
    audit = AuditTrailView.from_cycle_dir(cycle_dir)
    session.state.audit_projection = audit
    pobb = PoBBStreamView.from_cycle_dir(cycle_dir)

    ledger = CycleEventLog.open(cycle_dir)
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
    if fork is None:
        dashboard = build_campaign_emitter(
            session,
            campaign_config,
            origin_accuracy=origin_accuracy,
            resumed_from_round=resumed_from_round,
            recorder=audit,
            langfuse_trace_url=trace_url,
        )
    else:
        dashboard = build_campaign_emitter(
            session,
            campaign_config,
            origin_accuracy=origin_accuracy,
            resumed_from_round=resumed_from_round,
            recorder=audit,
            seed_from_cycle_id=fork.parent_cycle_id,
            langfuse_trace_url=trace_url,
        )
        parent_dir = CycleDir(
            session.store.campaigns.cycle_dir(
                CycleHop(campaign_id=session.campaign_id, cycle_id=fork.parent_cycle_id)
            )
        )
        fresh_parent = CycleEventLog.open(parent_dir)
        ledger.inherit_from(fresh_parent, fresh_parent.next_offset)

    # `for_session` answers `None` on an incomplete address — the two halves the guard above
    # already covers, plus `tenant_root` / `session_id`. Binding that None to the ledger would
    # raise here anyway; saying which field is empty costs one line and names the cause.
    if dashboard is None:
        raise RuntimeError(
            "build_run_observers: LiveDashboardView.for_session returned None — the session "
            f"address is incomplete (tenant_root={session.tenant_root!r}, "
            f"session_id={session.session_id!r}, hop={session.hop})"
        )

    # Profile A — the outbound SSE highway is served by tailing the on-disk
    # ledger (``CycleLedgerTail``), cross-process, so there's nothing to register
    # here. The runner just appends; any reader (the API server, the CLI, a
    # future MCP client) tails ``.runtime/ledger.jsonl`` directly.
    ledger.bind(dashboard)
    ledger.bind(audit)
    if display is not None:
        ledger.bind(display)
    ledger.bind(pobb)
    session.state.ledger = ledger

    callbacks = RunCallbacks(ledger=ledger)
    # Bind the ledger into the per-asyncio-task ContextVar so emit_token_usage
    # finds it without a process-global sink. Token rides on RunObservers so
    # drain_all can restore the prior context on teardown.
    ledger_token = set_cycle_ledger(ledger)

    return RunObservers(
        callbacks=callbacks,
        audit=audit,
        dashboard=dashboard,
        pobb=pobb,
        display=display,
        _ledger_token=ledger_token,
    )
