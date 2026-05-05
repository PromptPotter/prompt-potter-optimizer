"""ProjectionBase — typed-record routing for ledger subscribers.

Every concrete projection in this package isinstance-dispatches the same
three ``CycleRecord`` subtypes (``PhaseRecord`` / ``SnapshotRecord`` / ``DecisionRecord``).
This base owns that dispatch in one place: subclasses override only the
hooks they care about, and adding a new ``CycleRecord`` subtype touches one
file instead of N. Default hooks are no-ops so writers that don't care
about a kind stay silent without boilerplate.
"""

from __future__ import annotations

from promptpotter.domain.run_records import CycleRecord, DecisionRecord, PhaseRecord, SnapshotRecord

__all__ = ["ProjectionBase"]


class ProjectionBase:
    def on_record(self, record: CycleRecord, offset: int) -> None:
        del offset
        if isinstance(record, PhaseRecord):
            self._handle_phase(record)
        elif isinstance(record, SnapshotRecord):
            self._handle_snapshot(record)
        elif isinstance(record, DecisionRecord):
            self._handle_decision(record)

    def _handle_phase(self, record: PhaseRecord) -> None: ...
    def _handle_snapshot(self, record: SnapshotRecord) -> None: ...
    def _handle_decision(self, record: DecisionRecord) -> None: ...
