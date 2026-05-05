"""Optimization callbacks → CycleLedger records — eager-bound, single sink."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from promptpotter.domain.run_records import PhaseRecord, SnapshotRecord
from promptpotter.infrastructure.ledger import CycleLedger
from promptpotter.presentation.views.view_factories import (
    from_phase_event,
    view_to_wire_dict,
)
from promptpotter.shared.errors import graceful

__all__ = ["RunCallbacks"]


@dataclass
class RunCallbacks:
    """Single ingress: callbacks → typed CycleRecord → ``CycleLedger.append``.

    Subscribers consume via ``on_record`` on the bound ledger. PhaseRecord-view
    ctx is owned here (``from_phase_event`` is stateful) and serialised onto
    ``PhaseRecord.payload['view']``. Ledger is required at construction — no
    deferred binding, no buffer.
    """

    ledger: CycleLedger
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

    def on_sample_started(self, ci: int, ct: int, qi: int, qt: int, query_text: str) -> None:
        self._snapshot(
            "sample_started", ci, ct, {"query_text": query_text}, sample_idx=qi, sample_total=qt
        )

    def on_sample_scored(self, ci: int, ct: int, qi: int, qt: int, result: dict) -> None:
        self._snapshot("sample_scored", ci, ct, {"result": result}, sample_idx=qi, sample_total=qt)

    def on_p_best_update(self, round_num: int, ci: int, ct: int, snapshot: Any) -> None:
        """Per-query PoBB snapshot — archive-only, not divergence-gated."""
        self._snapshot(
            "p_best_update",
            ci,
            ct,
            {
                "current_id": str(snapshot.current_id),
                "n_queries": int(snapshot.n_queries),
                "p_best": dict(snapshot.p_best),
            },
            round_num=round_num,
            sample_idx=int(snapshot.n_queries) - 1,
        )

    def set_round(self, round_num: int) -> None:
        self._current_round = round_num
