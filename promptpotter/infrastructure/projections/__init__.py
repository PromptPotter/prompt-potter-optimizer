"""Projections — subscribers to the ``CycleEventLog`` spine.

Each module here owns one view over the ledger's record stream:

* :mod:`live_dashboard` — the operator-facing ``dashboard.json`` writer,
  session-family-bound (one file per session, written into the session
  root cycle dir and shared by that session's forks). Constructor takes
  ``SessionFamilyDir`` so an audit block cannot accidentally land here.
* :mod:`audit_trail` — the per-round node I/O recorder that flushes to
  ``campaigns/{campaign_id}/cycles/{cycle_id}/.runtime/cache/rounds/round_NNNN.json``.
  Per-cycle scope; constructor takes ``CycleDir`` so a fork's recorder
  cannot accidentally write to a sibling's tree.
* :mod:`pobb_stream` — appends per-sample P(best) updates to the cycle's
  ``.runtime/streams/round_NNNN_p_best.jsonl``.
"""

from promptpotter.infrastructure.projections.audit_trail import AuditTrailView
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
    "LiveDashboardView",
    "LiveStateCore",
    "PoBBStreamView",
    "apply_p_best_update",
    "apply_phase",
    "roll_p_best_at_round_complete",
    "top_n_p_best",
]
