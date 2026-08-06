"""The closed outbound SSE set declared in ``docs/specs/m12-events-asyncapi.yaml``. ``sequence`` is the ledger
offset — a subscriber detects gaps by it and replays from the family ray, never via a ``since=`` on the tail."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from promptpotter.domain.strict_model import StrictModel

__all__ = ["ProjectionEnvelope", "ProjectionKind"]


# Closed enum mirroring ``ProjectionEnvelope.kind`` in
# ``docs/specs/m12-events-asyncapi.yaml``. It MUST cover the whole ``CycleRecord``
# union — all thirteen ``record_type`` literals — plus one projection-only kind.
# Keep this set in sync with the YAML by hand — drift fails loud (an
# unknown kind raises on dispatch); no standing test (see tests/CLAUDE.md).
#
# **A missing kind is a HOLE, not a filter, and that is why coverage is the rule.**
# ``CycleLedgerTail.read_new`` advances ``_line_index`` for every line it reads,
# including one whose kind it cannot map — so an unlisted record is skipped while
# still consuming an offset, and the client sees a ``sequence`` gap and fires the
# reconnect recipe in ``docs/developer/event-stream.md``. ``candidate_minted`` and
# ``cycle_seed`` were absent here for exactly that reason and produced exactly that.
# With full coverage, ``_to_envelope`` returns ``None`` only for a genuinely
# malformed line — which IS a gap worth noticing.
ProjectionKind = Literal[
    # record_type literals (13) — the complete `CycleRecord` union
    "candidate_minted",
    "decision",
    "command",
    "command_ack",
    "cycle_seed",
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


class ProjectionEnvelope(StrictModel):
    """One outbound SSE frame. Frozen wire shape — a receiver MUST treat an unknown field as a DRIFT SIGNAL, not as
    forward-compat slack, and a new field requires the AsyncAPI update first."""

    model_config = ConfigDict(frozen=True)

    kind: ProjectionKind = Field(
        description="Closed-set discriminator; every CycleRecord record_type, plus stream_snapshot.",
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
