"""Resume-checkpoint policy — gating modes + ledger-append helper.

``DECISION_GATING`` is the single source of truth for whether a
:class:`DecisionKind` drives resume-divergence checking
(``REPLAYED`` — re-derived under the active scorer; mismatch halts or
forks) or is archival only (``ARCHIVAL`` — written for audit, never
compared on resume). Every member of :class:`DecisionKind` MUST appear
here exactly once; an import-time check fires before this module loads
otherwise.

Adding a kind: extend :class:`DecisionKind` in
``promptpotter.domain.run_records`` (the data shape) AND extend
``DECISION_GATING`` in the same commit. ``REPLAYED`` kinds also need a
registered replayer in :mod:`.replayers`.

The ``DecisionRecord`` and ``DecisionKind`` types stay in
``domain.run_records`` so the ``CycleRecord`` discriminated union owns
them; this module re-exports them so callers reach for one resume+fork
package instead of mixing imports.
"""

from __future__ import annotations

import enum
from typing import Any, Protocol

from promptpotter.domain.run_records import DecisionKind, DecisionRecord

__all__ = [
    "DECISION_GATING",
    "DecisionKind",
    "DecisionRecord",
    "GatingMode",
    "record_decision",
]


class GatingMode(enum.StrEnum):
    """Whether a decision kind drives resume-divergence checking.

    REPLAYED: re-derived under the active scorer on resume; mismatch halts
    or forks. ARCHIVAL: archived only; never compared on resume.
    """

    REPLAYED = "replayed"
    ARCHIVAL = "archival"


# Single source of truth for which kinds are divergence-gated. Every
# ``DecisionKind`` member MUST appear here exactly once. ``REPLAYED`` kinds
# also need a registered replayer (see :mod:`.replayers`); ``ARCHIVAL``
# kinds must NOT have one.
DECISION_GATING: dict[DecisionKind, GatingMode] = {
    DecisionKind.ROUND_WINNER: GatingMode.REPLAYED,
    DecisionKind.ELIMINATION_CUT: GatingMode.REPLAYED,
    DecisionKind.LEADER_LOCK_IN: GatingMode.REPLAYED,
    DecisionKind.L2_ESCALATION_TRIGGER: GatingMode.REPLAYED,
    DecisionKind.L3_ESCALATION_TRIGGER: GatingMode.REPLAYED,
    DecisionKind.PROBE_ROUND_COMMITMENT: GatingMode.ARCHIVAL,
    # Fork is observable from the parent's history (the FORK_CUT record in
    # the parent ledger names the new cycle id and the offset that the
    # fork inherits from). It's archival because the fork's identity is
    # downstream of the divergence-checked decisions, not part of the
    # gating itself — replaying it can't re-derive a different fork.
    DecisionKind.FORK_CUT: GatingMode.ARCHIVAL,
}


# Adding a kind without choosing REPLAYED/ARCHIVAL is a programming error;
# fail at import rather than at first replay attempt.
_unmapped = [k for k in DecisionKind if k not in DECISION_GATING]
if _unmapped:
    raise RuntimeError(f"DecisionKind members missing from DECISION_GATING: {_unmapped}")
del _unmapped


class _DecisionSink(Protocol):
    """Anything with ``append(DecisionRecord) -> Any`` — list[DecisionRecord] or CycleLedger."""

    def append(self, decision: DecisionRecord, /) -> Any: ...


def record_decision(
    sink: _DecisionSink,
    kind: DecisionKind,
    inputs_ref: dict[str, Any],
    outcome: Any,
    *,
    data: dict[str, Any] | None = None,
    round: int | None = None,
) -> Any:
    """Build a DecisionRecord and append to *sink*; return outcome for passthrough."""
    sink.append(
        DecisionRecord(
            kind=kind,
            inputs_ref=dict(inputs_ref),
            outcome=outcome,
            data=dict(data or {}),
            round=round,
        )
    )
    return outcome
