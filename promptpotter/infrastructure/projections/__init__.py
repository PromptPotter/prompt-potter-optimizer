"""Projections — subscribers to the ``RunLedger`` spine.

Each module here owns one view over the ledger's record stream:

* :mod:`live_dashboard` — the operator-facing ``dashboard.json`` writer,
  family-root-bound (one file per cycle family, shared across forks).
  Constructor takes ``RootCycleDir`` so an audit block cannot
  accidentally land here.
* :mod:`audit_trail` — the per-round node I/O recorder that flushes to
  ``campaigns/{cycle_id}/.runtime/cache/rounds/round_NNNN.json``. Per-cycle
  scope; constructor takes ``CycleDir`` so a fork's recorder cannot
  accidentally write to the parent's tree.
* :mod:`journal` — operator narrative helpers (``journal.md`` +
  ``notes.md`` under ``sessions/{session_id}/``). Session-scoped, no
  ledger subscription.
"""

from promptpotter.infrastructure.projections.audit_trail import AuditTrailProjection
from promptpotter.infrastructure.projections.journal import (
    append_journal,
    read_claude_notes,
)
from promptpotter.infrastructure.projections.live_dashboard import LiveDashboardProjection
from promptpotter.infrastructure.projections.pobb_stream import PoBBStreamProjection

__all__ = [
    "AuditTrailProjection",
    "LiveDashboardProjection",
    "PoBBStreamProjection",
    "append_journal",
    "read_claude_notes",
]
