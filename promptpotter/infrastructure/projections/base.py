"""ProjectionBase — typed-record routing for ledger subscribers.

Every concrete projection in this package isinstance-dispatches the same
three ``RunRecord`` subtypes (``Phase`` / ``Snapshot`` / ``Decision``).
This base owns that dispatch in one place: subclasses override only the
hooks they care about, and adding a new ``RunRecord`` subtype touches one
file instead of N. Default hooks are no-ops so writers that don't care
about a kind stay silent without boilerplate.
"""

from __future__ import annotations

from promptpotter.domain.run_records import Decision, Phase, RunRecord, Snapshot

__all__ = ["ProjectionBase"]


class ProjectionBase:
    def on_record(self, record: RunRecord, offset: int) -> None:
        del offset
        if isinstance(record, Phase):
            self._handle_phase(record)
        elif isinstance(record, Snapshot):
            self._handle_snapshot(record)
        elif isinstance(record, Decision):
            self._handle_decision(record)

    def _handle_phase(self, record: Phase) -> None: ...
    def _handle_snapshot(self, record: Snapshot) -> None: ...
    def _handle_decision(self, record: Decision) -> None: ...
