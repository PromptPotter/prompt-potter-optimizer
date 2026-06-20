"""Projections — subscribers to the ``CycleEventLog`` spine.

Every view here extends :class:`base.DerivedView <.base>`, which owns the
single ``isinstance(record, …)`` dispatch over the record stream; subclasses
override only the hooks they care about (adding a ``CycleRecord`` subtype
touches one file). No second dispatch path exists.

CONCEPT MAP (one view per module):
* :class:`LiveDashboardView` (:mod:`.live_dashboard`) — operator-facing
  ``dashboard.json`` writer, per-cycle (every cycle — root/fork/sweep/diag —
  owns its file, stamped with its ``cycle_id``). Built on the pure
  ``live_state`` core (``LiveStateCore`` + ``apply_phase`` /
  ``apply_p_best_update`` / ``roll_p_best_at_round_complete`` / ``top_n_p_best``).
* :class:`AuditTrailView` (:mod:`.audit_trail`) — per-round node I/O recorder
  flushing ``.runtime/cache/rounds/round_NNNN.json`` (the only buffering view).
* :class:`PoBBStreamView` (:mod:`.pobb_stream`) — appends per-sample P(best)
  updates to ``.runtime/streams/round_NNNN_p_best.jsonl``.

The outbound SSE stream is NOT a ledger subscriber — it tails the on-disk
``.runtime/ledger.jsonl`` cross-process via
:class:`event_stream.CycleLedgerTail <.event_stream>`, so the writer side here
just appends to the ledger like everything else; no in-process fan-out.
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
