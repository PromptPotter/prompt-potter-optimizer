"""``ProjectionEnvelope`` — the wire frame for outbound SSE projections.

The closed outbound set declared in ``docs/specs/m12-events-asyncapi.yaml``.
Every Server-Sent Events frame on the cycle stream is a ``ProjectionEnvelope``
(security box 13). The envelope wraps either a ledger ``CycleRecord``
(``kind`` = its ``record_type``) or a projection-synthesized payload
(``stream_snapshot`` at subscribe time).

``sequence`` is the ledger offset at which the underlying record was
appended; subscribers detect gaps by sequence and request replay from a
later profile's replay endpoint. ``cycle_id`` redundantly stamps the
target cycle so multi-cycle clients can demultiplex a fan-in subscription.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from promptpotter.shared.clock import utcnow_iso

__all__ = ["ProjectionEnvelope", "ProjectionKind"]


# Closed enum mirroring ``ProjectionEnvelope.kind`` in
# ``docs/specs/m12-events-asyncapi.yaml``. Eleven entries match
# ``record_type`` literals on ``CycleRecord``; one is projection-only.
# Keep this set in sync with the YAML by hand — drift fails loud (an
# unknown kind raises on dispatch); no standing test (see tests/CLAUDE.md).
ProjectionKind = Literal[
    # record_type literals (11)
    "decision",
    "command",
    "command_ack",
    "error",
    "llm_call_progress",
    "llm_call",
    "llm_call_start",
    "phase",
    "round_warning",
    "snapshot",
    "token_usage",
    # projection-only (1) — synthesized by the ledger tail (``CycleLedgerTail``)
    "stream_snapshot",
]


class ProjectionEnvelope(BaseModel):
    """One outbound SSE frame.

    Frozen wire shape — receivers MUST treat unknown future fields as a
    drift signal, not as forward-compat slack. New fields require an
    AsyncAPI YAML update first (security box 1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ProjectionKind = Field(
        description="Closed-set discriminator; one of seven record_type literals or three projection-only kinds.",
    )
    version: int = Field(
        default=1,
        description="Envelope shape version. Bump only on a breaking restructure of this class; payload churn is per-kind.",
    )
    cycle_id: str = Field(
        description="Target cycle the frame describes; redundant with the channel address but stamped per-frame for fan-in demux.",
    )
    sequence: int = Field(
        ge=0,
        description="Ledger offset at append. Live-tail frames carry the record's offset; the leading stream_snapshot frame carries the offset captured at subscribe time.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-kind body. For record-derived kinds, the record's model_dump; for stream_snapshot, the dashboard.json content + snapshot_at_offset.",
    )
    emitted_at: str = Field(
        default_factory=utcnow_iso,
        description="ISO-8601 wall-clock at the moment the envelope was minted; for debugging only — never load-bearing for ordering.",
    )
