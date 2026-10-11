from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import ConfigDict, Field, ValidationError

from promptpotter.domain.activity import SILENT_RECORDS, ActivityState
from promptpotter.domain.run_records import RECORD_ADAPTER, CycleRecord
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "NON_ACTIVITY_KINDS",
    "RECORD_KINDS",
    "ProjectionEnvelope",
    "ProjectionKind",
    "bearing_record",
]


# A missing kind is a HOLE, not a filter: the tail still consumes its offset, and the client reconnects.
ProjectionKind = Literal[
    "backend_warning",
    "candidate_minted",
    "candidate_scored",
    "candidate_started",
    "checkin_closed",
    "decision",
    "command",
    "command_ack",
    "cycle_final",
    "cycle_minted",
    "cycle_seed",
    "cycle_superseded",
    "election",
    "error",
    "flight",
    "fork_graded",
    "intervention",
    "launch_claim",
    "launch_released",
    "llm_call_progress",
    "llm_call",
    "llm_call_start",
    "optimizer_state",
    "phase",
    "priced_key",
    "race_catch_up",
    "race_standing",
    "round_closed",
    "round_entered",
    "round_proposed",
    "round_standing",
    "round_warning",
    "ruler",
    "run_limits",
    "run_phase",
    "run_wiring",
    "sample_order",
    "sample_scored",
    "sample_started",
    "scoring_locked",
    "spawned",
    "spend_hold",
    "spend_tombstone",
    "token_usage",
    # Synthesized by the ledger tail (``CycleLedgerTail``); on no ledger.
    "stream_snapshot",
]

_PROJECTION_ONLY = frozenset({"stream_snapshot"})
_record_types = frozenset(
    arm.model_fields["record_type"].default for arm in get_args(get_args(CycleRecord)[0])
)
_declared = frozenset(get_args(ProjectionKind)) - _PROJECTION_ONLY
if _declared != _record_types:
    raise RuntimeError(
        "ProjectionKind must cover the CycleRecord union exactly — "
        f"missing {sorted(_record_types - _declared)}, "
        f"unbacked {sorted(_declared - _record_types)}."
    )

RECORD_KINDS: frozenset[str] = _record_types

#: The licence not to validate the record at all; a kind outside it may still yield no item.
NON_ACTIVITY_KINDS: frozenset[str] = frozenset(
    arm.model_fields["record_type"].default for arm in SILENT_RECORDS
)

del _record_types, _declared


def bearing_record(kind: str, rec: dict[str, Any]) -> CycleRecord | None:
    """``None`` for a kind that never renders, and for a line no arm accepts (``CycleEventLog.iter``'s skip)."""
    if kind in NON_ACTIVITY_KINDS:
        return None
    try:
        return RECORD_ADAPTER.validate_python(rec)
    except ValidationError:
        return None


class ProjectionEnvelope(StrictModel):
    """One outbound SSE frame, whose receiver treats an unknown field as a drift signal."""

    model_config = ConfigDict(frozen=True)

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
