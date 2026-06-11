"""Run observers + callbacks — single ingress for CLI/notebook/webapp.

- ``RunCallbacks`` — typed-event constructor over ``CycleEventLog.append`` (writer API).
- ``build_run_observers`` — wires audit + live dashboard + PoBB stream +
  optional ``LiveDisplay`` to one ledger, auto-mints session+cycle, re-anchors on fork.
"""

from __future__ import annotations

from contextvars import Token
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.session import auto_mint_session
from promptpotter.application.origin import (
    build_campaign_emitter,
    resolve_origin_opt_search_point,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import (
    reset_current_round,
    reset_cycle_ledger,
    set_current_round,
    set_cycle_ledger,
)
from promptpotter.infrastructure.projections import (
    AuditTrailView,
    EventStreamView,
    LiveDashboardView,
    PoBBStreamView,
    deregister_event_stream,
    register_event_stream,
)
from promptpotter.presentation.views.view_ingress import from_phase_event
from promptpotter.presentation.views.view_models import ViewContext
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.views.live import LiveDisplay

__all__ = ["ForkInfo", "RunCallbacks", "RunObservers", "build_run_observers"]


# Keys in ``PhaseEvent.data`` that carry live runtime references the JSON
# serializer can't walk: ``env`` (the ``Session`` — BackendStore +
# LangfuseLogger) and ``state`` (the live ``Cycle``, which reaches the same
# handles via its ledger + scoring wiring). View derivation in
# ``RunCallbacks.on_phase`` consumes them before they're stripped, and the two
# ledger subscribers that read origin state at INIT:exit (LiveDashboardView,
# LiveDisplay) now take it off ``payload['view']`` instead — so nothing reads
# these off the persisted/streamed record. Without stripping ``state`` the SSE
# projection's ``model_dump(mode="json")`` raises on the BackendStore.
_DATA_KEYS_RUNTIME_ONLY = frozenset({"env", "state"})


@dataclass
class RunCallbacks:
    """Single ingress: callbacks → typed CycleRecord → ``CycleEventLog.append``.

    Ledger required at construction (no deferred binding). PhaseRecord-view ctx
    is owned here (``from_phase_event`` is stateful); the typed view it returns
    rides ``PhaseRecord.payload['view']`` and Pydantic serialises it on persist.

    ``_round_token`` is the reset handle for the ``_CURRENT_ROUND`` ContextVar
    that ``emit_token_usage`` reads — set on every ``set_round`` call so the
    next emission stamps the correct round; reset on ``drain_all`` so the
    process doesn't leak round state into the next cycle.
    """

    ledger: CycleEventLog
    _phase_ctx: ViewContext = field(default_factory=ViewContext)
    _current_round: int = 0
    _round_token: Token[int | None] | None = field(default=None, init=False, repr=False)

    def _emit(self, record: Any) -> None:
        with graceful("ledger append failed"):
            self.ledger.append(record)

    def on_phase(self, event: Any) -> None:
        view = from_phase_event(event, self._phase_ctx)
        # View derivation has already consumed any live runtime references
        # carried in ``event.data`` (the most opaque one is ``env=session``
        # which holds the BackendStore + LangfuseLogger handles those classes
        # can't serialize). Strip them before the record lands on the ledger so
        # ``EventStreamView.on_record`` can ``model_dump(mode="json")`` and the
        # on-disk dump stay clean. The typed ``view`` rides the in-memory
        # fan-out (subscribers read it directly); Pydantic serializes it to its
        # wire dict on persist + SSE. ``payload["data"]`` is only consumed live,
        # in-memory, where the caller still holds the originals.
        safe_data = {k: v for k, v in event.data.items() if k not in _DATA_KEYS_RUNTIME_ONLY}
        self._emit(
            PhaseRecord(
                phase=str(event.phase),
                event=str(event.event),
                round=event.round,
                payload={"view": view, "data": safe_data},
            )
        )

    def on_round_complete(self, round_result: Any, l1_stall_count: int) -> None:
        # ``event="display"`` keeps ``EscalationFSM.fold`` reading only the lean ``event="complete"`` audit emit.
        self._phase_ctx.l1_stall_count = l1_stall_count
        self._emit(
            PhaseRecord(
                phase="round",
                event="display",
                round=round_result.round,
                payload={
                    "round_result": round_result,
                    "l1_stall_count": l1_stall_count,
                    "phase_ctx": asdict(self._phase_ctx),
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
    ) -> None:
        # `prompt_fields` + `pp_override` are the candidate's evolved searchpoint
        # (the seed-able half), surfaced live so the steer panel can fork from a
        # still-in-flight candidate without the round file — the in-flight peer
        # of round_NNNN.json::candidate_scores.
        self._snapshot(
            "candidate_started",
            idx,
            total,
            {
                "changes_description": changes_description,
                "pp_override": pp_override,
                "prompt_fields": prompt_fields,
            },
        )

    def on_candidate_scored(self, idx: int, total: int, scores: dict[str, Any]) -> None:
        self._snapshot(
            "candidate_scored",
            idx,
            total,
            {"scores": scores, "phase_ctx": asdict(self._phase_ctx)},
        )

    def on_sample_started(
        self, ci: int, ct: int, query_text: str, qi: int, qt: int, sample_id: int
    ) -> None:
        self._snapshot(
            "sample_started",
            ci,
            ct,
            {"query_text": query_text, "sample_id": int(sample_id)},
            sample_idx=qi,
            sample_total=qt,
        )

    def on_sample_scored(self, ci: int, ct: int, result: dict[str, Any], qi: int, qt: int) -> None:
        self._snapshot("sample_scored", ci, ct, {"result": result}, sample_idx=qi, sample_total=qt)

    def on_p_best_update(self, round_num: int, ci: int, ct: int, snapshot: Any) -> None:
        """Per-sample PoBB snapshot — archive-only, not divergence-gated."""
        self._snapshot(
            "p_best_update",
            ci,
            ct,
            {
                "current_id": str(snapshot.current_id),
                "n_samples": int(snapshot.n_samples),
                "p_best": dict(snapshot.p_best),
                "paired_breakdown": dict(snapshot.paired_breakdown or {}),
            },
            round_num=round_num,
            sample_idx=int(snapshot.n_samples) - 1,
        )

    def on_sample_order_preview(
        self,
        round_num: int,
        ci: int,
        ct: int,
        preview: list[tuple[int, float]],
        n_priors: int,
        sample_order: list[int],
    ) -> None:
        """Adaptive-queue-mechanism preview at candidate start — top-3 next samples + full
        expected-info-gain order under the prior (1PL Rasch CAT) so the webapp
        table can re-sort live. Distinct from the heatmap's hardest-first
        ``sample_order`` (absolute difficulty vs expected info value here).
        """
        self._snapshot(
            "sample_order_preview",
            ci,
            ct,
            {
                "preview": [[int(sid), float(delta)] for sid, delta in preview],
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
        """Update the current-round marker stamped onto every subsequent
        ``TokenUsageRecord`` via the ``_CURRENT_ROUND`` ContextVar.

        We always ``set`` a fresh value rather than re-using the prior token —
        the reset hand-off lives on ``drain_all`` (one reset per cycle), not
        per-round. The previous reset token is dropped on the floor on
        purpose: only the first ``set`` carries a meaningful default-restore
        token, and that's the one ``drain_all`` keeps."""
        self._current_round = round_num
        token = set_current_round(round_num)
        if self._round_token is None:
            self._round_token = token


@dataclass(frozen=True)
class RunObservers:
    """Frozen bundle: callbacks + projections + display, all bound to one ledger.

    ``_ledger_token`` is the reset handle for the ``_CYCLE_LEDGER`` ContextVar
    that ``emit_token_usage`` reads — set on construction so the next emission
    finds the ledger; reset on ``drain_all`` so the process doesn't leak ledger
    state into the next cycle (or into background tasks holding stale refs).
    """

    callbacks: RunCallbacks
    audit: AuditTrailView
    dashboard: LiveDashboardView
    pobb: PoBBStreamView
    event_stream: EventStreamView
    display: LiveDisplay | None
    _ledger_token: Token[CycleEventLog | None] | None = None
    _registry_key: tuple[str, str] | None = None

    def drain_all(self) -> None:
        """``drain()`` every projection + reset both emission ContextVars; called
        by ``_finalize_run`` on every stop reason so the audit cache reflects
        the ledger even on interrupt and no stale ledger ref survives the
        cycle."""
        self.audit.drain()
        self.dashboard.drain()
        self.pobb.drain()
        # event_stream.drain() closes every live SSE subscriber so HTTP
        # handlers exit cleanly; deregister so subsequent /events:subscribe
        # calls return 404 (cycle no longer running).
        self.event_stream.drain()
        if self._registry_key is not None:
            deregister_event_stream(*self._registry_key)
        if self._ledger_token is not None:
            reset_cycle_ledger(self._ledger_token)
        if self.callbacks._round_token is not None:
            reset_current_round(self.callbacks._round_token)
            self.callbacks._round_token = None


@dataclass(frozen=True)
class ForkInfo:
    """Forked-cycle wiring: the parent cycle id to seed the fork's own
    dashboard from (the fork gets a fresh per-cycle ``dashboard.json``)."""

    parent_cycle_id: str


def _ensure_session_minted(
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    *,
    experiment_id: str | None,
    origin_accuracy: float,
) -> None:
    """Auto-mint session+cycle from origin OSP if missing (idempotent)."""
    from promptpotter.application.runner import build_origin_cycle_id

    # Auto-mint is the genuine `new` flow only (no existing campaign). A
    # resume/fork launch binds identity before this runs, so either field being
    # set means the campaign already exists — never re-mint over it.
    if session.session_id or session.campaign_id:
        return

    prompt_nodes = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    resolved_origin = resolve_origin_opt_search_point(
        session.experiment_extract or {},
        prompt_node_names=prompt_nodes,
        dataset_dir=session.dataset_config_dir,
    )
    auto_mint_session(
        session,
        campaign_config,
        cycle_id=build_origin_cycle_id(
            resolved_origin, session.pipeline_schema, dataset, session.pipeline_params
        ),
        origin_acc=origin_accuracy,
        origin_prompt_fields=resolved_origin.prompt_field_dict(),
        dataset_size=len(dataset),
        experiment_id=experiment_id,
    )


def build_run_observers(
    *,
    session: Session,
    campaign_config: CampaignConfig,
    dataset: list[Sample],
    display: LiveDisplay | None = None,
    experiment_id: str | None = None,
    resumed_from_round: int | None = None,
    origin_accuracy: float = 0.0,
    fork: ForkInfo | None = None,
) -> RunObservers:
    """Mint session if needed; open ledger; build + bind every observer.

    ``fork=None`` ⇒ fresh cycle (new dashboard in its own cycle dir).
    ``fork=ForkInfo(...)`` ⇒ fresh per-cycle dashboard for the fork, seeded
    from the parent's on-disk ``dashboard.json`` (drained by ``_finalize_run``
    before this call), and inherit ledger from parent's current offset.
    ``origin_accuracy`` is a seed; real value lands on the next ``INIT/exit``
    event.
    """
    if fork is None:
        _ensure_session_minted(
            session,
            campaign_config,
            dataset,
            experiment_id=experiment_id,
            origin_accuracy=origin_accuracy,
        )

    if session.state.cycle_id is None or session.store is None:
        raise RuntimeError("build_run_observers: session must have cycle_id and store")

    cycle_dir = CycleDir(
        session.store.campaigns.cycle_dir(session.campaign_id, session.state.cycle_id)
    )
    audit = AuditTrailView.from_cycle_dir(cycle_dir)
    session.state.audit_projection = audit
    pobb = PoBBStreamView.from_cycle_dir(cycle_dir)

    ledger = CycleEventLog.open(cycle_dir)
    if fork is None:
        dashboard = build_campaign_emitter(
            session,
            campaign_config,
            origin_accuracy=origin_accuracy,
            resumed_from_round=resumed_from_round,
            recorder=audit,
        )
    else:
        dashboard = build_campaign_emitter(
            session,
            campaign_config,
            origin_accuracy=origin_accuracy,
            resumed_from_round=resumed_from_round,
            recorder=audit,
            seed_from_cycle_id=fork.parent_cycle_id,
        )
        parent_dir = CycleDir(
            session.store.campaigns.cycle_dir(session.campaign_id, fork.parent_cycle_id)
        )
        fresh_parent = CycleEventLog.open(parent_dir)
        ledger.inherit_from(fresh_parent, fresh_parent.next_offset)

    # Profile A — outbound SSE highway. Initial offset = ledger.next_offset so
    # on-resume subscribers attach past the on-disk record history; live tail
    # picks up at the next append. Registered in the process-wide registry
    # under (campaign_id, cycle_id) so the SSE endpoint can find it.
    event_stream = EventStreamView(
        cycle_dir, cycle_id=session.state.cycle_id, initial_offset=ledger.next_offset
    )
    registry_key = (session.campaign_id, session.state.cycle_id)
    register_event_stream(*registry_key, view=event_stream)

    ledger.bind(dashboard)
    ledger.bind(audit)
    if display is not None:
        ledger.bind(display)
    ledger.bind(pobb)
    ledger.bind(event_stream)
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
        event_stream=event_stream,
        display=display,
        _ledger_token=ledger_token,
        _registry_key=registry_key,
    )
