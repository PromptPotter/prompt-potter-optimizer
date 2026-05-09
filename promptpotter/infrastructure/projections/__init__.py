"""Projections — subscribers to the ``CycleLedger`` spine.

Each module here owns one view over the ledger's record stream:

* :mod:`live_dashboard` — the operator-facing ``dashboard.json`` writer,
  family-root-bound (one file per cycle family, shared across forks).
  Constructor takes ``RootCycleDir`` so an audit block cannot
  accidentally land here.
* :mod:`audit_trail` — the per-round node I/O recorder that flushes to
  ``campaigns/{cycle_id}/.runtime/cache/rounds/round_NNNN.json``. Per-cycle
  scope; constructor takes ``CycleDir`` so a fork's recorder cannot
  accidentally write to the parent's tree.
* :mod:`signals` — appends cadence rule firings to
  ``campaigns/{cycle_id}/.runtime/signals.jsonl``. Per-cycle scope;
  constructor takes ``CycleDir``.
"""

from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
from promptpotter.infrastructure.projections.live_dashboard import LiveDashboardProjection
from promptpotter.infrastructure.projections.live_state import (
    LiveStateCore,
    apply_p_best_update,
    apply_phase,
    roll_p_best_at_round_complete,
    top_n_p_best,
)
from promptpotter.infrastructure.projections.pobb_stream import PoBBStreamProjection
from promptpotter.infrastructure.projections.signals import SignalsProjection

__all__ = [
    "AuditTrailProjection",
    "LiveDashboardProjection",
    "LiveStateCore",
    "PoBBStreamProjection",
    "SignalsProjection",
    "apply_p_best_update",
    "apply_phase",
    "roll_p_best_at_round_complete",
    "top_n_p_best",
]
