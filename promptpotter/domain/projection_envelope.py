from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypeIs, get_args

from pydantic import Field

from promptpotter.domain.activity import ActivityRecord, ActivityState
from promptpotter.domain.record_kinds import RECORDS
from promptpotter.domain.records import CycleRecord
from promptpotter.domain.strict_model import FrozenModel

# A missing kind is a HOLE, not a filter: the tail still consumes its offset, and the client
# reconnects. ``stream_snapshot`` is synthesized by the ledger tail (``CycleLedgerTail``), on no ledger.
if TYPE_CHECKING:
    ProjectionKind = str
else:
    ProjectionKind = Literal[*RECORDS.registered(), "stream_snapshot"]

_ACTIVITY_ARMS: tuple[type, ...] = get_args(ActivityRecord)


def _renders(record: CycleRecord) -> TypeIs[ActivityRecord]:
    return isinstance(record, _ACTIVITY_ARMS)


def bearing_record(record: CycleRecord) -> ActivityRecord | None:
    """``None`` for a kind that never renders."""
    return record if _renders(record) else None


class ProjectionEnvelope(FrozenModel):
    """One outbound SSE frame, whose receiver treats an unknown field as a drift signal."""

    kind: ProjectionKind = Field(
        description="Closed-set discriminator; every CycleRecord record_type, plus stream_snapshot.",
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
        description="Per-kind body. For record-derived kinds, the record's model_dump; for stream_snapshot, the cycle's served dashboard.",
    )
    activity: ActivityState = Field(
        description="The run's current state as of this frame — what it is doing and what still holds. Whole on every frame, so a client renders the newest one and folds nothing.",
    )
