"""Run observers + callbacks — single ingress for CLI, notebook, future webapp.

Two halves of one lifecycle live here:

* ``RunCallbacks`` — typed event constructor over ``CycleEventLog.append``;
  the writer-side API the optimization loop calls into. Subscribers consume
  via ``on_record`` on the bound ledger.
* ``build_run_observers`` — wires every projection (audit trail, live
  dashboard, PoBB stream) plus an optional ``LiveDisplay`` to one ledger,
  auto-minting session+cycle if needed and re-anchoring on a fork. Returns
  a frozen ``RunObservers`` whose callbacks already hold the bound ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from promptpotter.application.bootstrap.session import auto_mint_session
from promptpotter.application.origin import (
    build_campaign_emitter,
    load_origin_prompt,
)
from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord, TokenUsageRecord
from promptpotter.infrastructure.ledger import CycleEventLog
from promptpotter.infrastructure.llm import TokenUsage
from promptpotter.infrastructure.projections import (
    AuditTrailView,
    LiveDashboardView,
    PoBBStreamView,
)
from promptpotter.presentation.views.view_ingress import (
    from_phase_event,
    view_to_wire_dict,
)
from promptpotter.shared.errors import graceful

if TYPE_CHECKING:
    from promptpotter.application.bootstrap.session import Session
    from promptpotter.application.config import CampaignConfig
    from promptpotter.domain.sample import Sample
    from promptpotter.presentation.views.live import LiveDisplay

__all__ = ["ForkInfo", "RunCallbacks", "RunObservers", "build_run_observers"]


@dataclass
class RunCallbacks:
    """Single ingress: callbacks → typed CycleRecord → ``CycleEventLog.append``.

    Subscribers consume via ``on_record`` on the bound ledger. PhaseRecord-view
    ctx is owned here (``from_phase_event`` is stateful) and serialised onto
    ``PhaseRecord.payload['view']``. Ledger is required at construction — no
    deferred binding, no buffer.
    """

    ledger: CycleEventLog
    _phase_ctx: dict[str, Any] = field(default_factory=dict)
    _current_round: int = 0

    def _emit(self, record: Any) -> None:
        with graceful("ledger append failed"):
            self.ledger.append(record)

    def on_phase(self, event: Any) -> None:
        view = view_to_wire_dict(from_phase_event(event, self._phase_ctx))
        self._emit(
            PhaseRecord(
                phase=str(event.phase),
                event=str(event.event),
                round=event.round,
                payload={"view": view, "data": event.data},
            )
        )

    def on_round_complete(self, round_result: Any, l1_stall_count: int) -> None:
        # Display-only emit: distinct ``event="display"`` so the audit emit
        # (``event="complete"``, lean scalars) is the sole input to
        # ``EscalationState.fold``. No payload-shape demultiplex.
        self._phase_ctx["l1_stall_count"] = l1_stall_count
        self._emit(
            PhaseRecord(
                phase="round",
                event="display",
                round=round_result.round,
                payload={
                    "round_result": round_result,
                    "l1_stall_count": l1_stall_count,
                    "phase_ctx": dict(self._phase_ctx),
                },
            )
        )

    def _snapshot(
        self,
        event: str,
        ci: int,
        ct: int,
        payload: dict,
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
        self, idx: int, total: int, changes_description: str, pp_override: dict | None
    ) -> None:
        self._snapshot(
            "candidate_started",
            idx,
            total,
            {"changes_description": changes_description, "pp_override": pp_override},
        )

    def on_candidate_scored(self, idx: int, total: int, scores: dict) -> None:
        self._snapshot(
            "candidate_scored",
            idx,
            total,
            {"scores": scores, "phase_ctx": dict(self._phase_ctx)},
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

    def on_sample_scored(self, ci: int, ct: int, result: dict, qi: int, qt: int) -> None:
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
        """PoBB sample-order preview — top-3 next samples by cross-candidate
        discrimination (``p*(1-p)``), plus the full discrimination-first
        ``sample_order`` so the webapp dataset table can re-sort live to
        mirror what the loop is iterating.

        Fired at the start of each candidate before scoring begins. The
        ``preview`` list is the head of PoBB's iteration order with each
        sample's discrimination score; ``n_priors`` is the number of
        completed candidate histories that fed the score; ``sample_order``
        is the full list, surfaced verbatim on
        ``dashboard.json::hard_sample_order``. The artifact's hardest-first
        ``sample_order`` lives next to this on disk for the heatmap; the
        two diverge by design — PoBB needs between-candidate variance,
        the heatmap needs absolute difficulty.
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
        backfilled: dict[str, list[str]],
    ) -> None:
        """Paired-PoBB leader backfill — per-prior list of newly-measured samples.

        Fired after ``PoBBCheck.backfill_priors`` resolves, only when at
        least one prior gained new measurements. Empty/no-op backfills
        (all priors already cover the candidate's sample order) are NOT
        emitted — the absence of an event means the cache covered it.
        Payload shape: ``{prior_id: [sample_id, ...]}``.
        """
        if not backfilled:
            return
        self._snapshot(
            "pobb_backfill",
            ci,
            ct,
            {"backfilled": {str(k): [str(s) for s in v] for k, v in backfilled.items()}},
            round_num=round_num,
        )

    def set_round(self, round_num: int) -> None:
        self._current_round = round_num

    def on_token_usage(self, usage: TokenUsage) -> None:
        """Forward an ``emit_token_usage`` event into the ledger.

        Installed as ``infrastructure.llm.set_token_usage_sink`` by the
        runner, so every optimizer LLM call's token + cost lands as a
        ``TokenUsageRecord``. The dashboard projection sums the records
        into ``dashboard.json::spend.loop``.
        """
        self._emit(
            TokenUsageRecord(
                kind=usage.kind,
                node=usage.node,
                model=usage.model,
                input_tokens=int(usage.input_tokens),
                output_tokens=int(usage.output_tokens),
                duration_s=float(usage.duration_s),
                cost_usd=usage.cost_usd,
                round=self._current_round,
            )
        )


@dataclass(frozen=True)
class RunObservers:
    """Frozen bundle: callbacks + projections + display, all bound to one ledger."""

    callbacks: RunCallbacks
    audit: AuditTrailView
    dashboard: LiveDashboardView
    pobb: PoBBStreamView
    display: LiveDisplay | None

    def drain_all(self) -> None:
        """Call ``drain()`` on every projection in the bundle.

        The runner invokes this in ``_finalize_run`` regardless of stop
        reason so the audit cache reflects whatever the ledger has, even
        when an interrupt prevented the round's ``round:complete`` event.
        Incremental projections (``LiveDashboardView``, ``PoBBStreamView``)
        inherit the no-op default.
        """
        self.audit.drain()
        self.dashboard.drain()
        self.pobb.drain()


@dataclass(frozen=True)
class ForkInfo:
    """Forked-cycle wiring: parent cycle id + family-root dashboard."""

    parent_cycle_id: str
    parent_dashboard: LiveDashboardView


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

    if session.session_id:
        return

    prompt_nodes = session.pipeline_schema.prompt_node_names() if session.pipeline_schema else []
    origin_osp = load_origin_prompt(
        session.experiment_extract or {},
        prompt_node_names=prompt_nodes,
        dataset_name=campaign_config.dataset_name,
    )
    auto_mint_session(
        session,
        campaign_config,
        cycle_id=build_origin_cycle_id(origin_osp, session.pipeline_schema, dataset),
        origin_acc=origin_accuracy,
        origin_prompt_fields=origin_osp.prompt_field_dict(),
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

    Pass ``fork=None`` for a fresh cycle (mints a new dashboard anchored at
    the family root). Pass ``fork=ForkInfo(...)`` when a fork-on-divergence
    has just minted a sibling cycle — the parent's dashboard is reattached
    to the fork's audit projection and the ledger inherits from the parent
    at its current offset. ``origin_accuracy`` is a seed for
    ``dashboard.json``; the real value lands on the next ``INIT/exit``
    phase event after origin runs.
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

    cycle_dir = CycleDir(session.store.campaigns.campaign_dir(session.state.cycle_id))
    audit = AuditTrailView.from_cycle_dir(cycle_dir)
    audit.rehydrate_sticky()
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
        dashboard = fork.parent_dashboard
        dashboard._recorder = audit
        dashboard.log_fork(
            old_cycle_id=fork.parent_cycle_id,
            new_cycle_id=session.state.cycle_id,
            from_round=resumed_from_round or 0,
        )
        parent_dir = CycleDir(session.store.campaigns.campaign_dir(fork.parent_cycle_id))
        fresh_parent = CycleEventLog.open(parent_dir)
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
