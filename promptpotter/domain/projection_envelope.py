"""The closed outbound SSE set declared in ``docs/specs/m12-events-asyncapi.yaml``. ``sequence`` is the ledger
offset — a subscriber detects gaps by it and replays from the family ray, never via a ``since=`` on the tail."""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.strict_model import StrictModel

__all__ = ["ProjectionEnvelope", "ProjectionKind"]


# Closed enum mirroring ``ProjectionEnvelope.kind`` in ``docs/specs/m12-events-asyncapi.yaml``.
#
# **A missing kind is a HOLE, not a filter.** ``CycleLedgerTail.read_new`` advances
# ``_line_index`` for every line it reads, including one whose kind it cannot map — so an
# unlisted record is skipped while still consuming an offset, and the client sees a ``sequence``
# gap and fires the reconnect recipe in ``docs/developer/event-stream.md``.
ProjectionKind = Literal[
    # `record_type` literals — the complete `CycleRecord` union
    "candidate_minted",
    "decision",
    "command",
    "command_ack",
    "cycle_seed",
    "election",
    "error",
    "llm_call_progress",
    "llm_call",
    "llm_call_start",
    "phase",
    "round_warning",
    "ruler",
    "snapshot",
    "spend_tombstone",
    "token_usage",
    # projection-only — synthesized by the ledger tail (``CycleLedgerTail``)
    "stream_snapshot",
]

# The coverage rule as an import-time raise, both directions: a `CycleRecord` arm with no kind is
# the silent offset-burning hole above, and a kind naming no arm is a wire promise nothing sends.
_PROJECTION_ONLY = frozenset({"stream_snapshot"})
_record_types = {
    arm.model_fields["record_type"].default for arm in get_args(get_args(CycleRecord)[0])
}
_declared = frozenset(get_args(ProjectionKind)) - _PROJECTION_ONLY
if _declared != _record_types:
    raise RuntimeError(
        "ProjectionKind must cover the CycleRecord union exactly — "
        f"missing {sorted(_record_types - _declared)}, "
        f"unbacked {sorted(_declared - _record_types)}."
    )
del _record_types, _declared


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
