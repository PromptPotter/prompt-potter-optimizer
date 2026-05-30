"""Projections — subscribers to the ``CycleEventLog`` spine.

Each module here owns one view over the ledger's record stream:

* :mod:`live_dashboard` — the operator-facing ``dashboard.json`` writer,
  per-cycle (every cycle — root, fork, sweep, diag — owns its own file in
  its own cycle dir, stamped with its own ``cycle_id``). Constructor takes
  ``CycleDir``.
* :mod:`audit_trail` — the per-round node I/O recorder that flushes to
  ``campaigns/{campaign_id}/cycles/{cycle_id}/.runtime/cache/rounds/round_NNNN.json``.
  Per-cycle scope; constructor takes ``CycleDir`` so a fork's recorder
  cannot accidentally write to a sibling's tree.
* :mod:`pobb_stream` — appends per-sample P(best) updates to the cycle's
  ``.runtime/streams/round_NNNN_p_best.jsonl``.
"""

from promptpotter.infrastructure.projections.audit_trail import AuditTrailView
from promptpotter.infrastructure.projections.event_stream import (
    EventStreamSubscriber,
    EventStreamView,
    deregister_event_stream,
    get_event_stream,
    register_event_stream,
)
from promptpotter.infrastructure.projections.live_dashboard import LiveDashboardView
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    apply_phase,
    roll_p_best_at_round_complete,
    top_n_p_best,
)
from promptpotter.infrastructure.projections.pobb_stream import PoBBStreamView

__all__ = [
    "AuditTrailView",
    "EventStreamSubscriber",
    "EventStreamView",
    "LiveDashboardView",
    "LiveStateCore",
    "PoBBStreamView",
    "apply_p_best_update",
    "apply_phase",
    "deregister_event_stream",
    "get_event_stream",
    "register_event_stream",
    "roll_p_best_at_round_complete",
    "top_n_p_best",
]
