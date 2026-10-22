from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError

from promptpotter.domain.activity import ActivityRecord, ActivityState, SilentRecord
from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.strict_model import FrozenModel

# A missing kind is a HOLE, not a filter: the tail still consumes its offset, and the client reconnects.
ProjectionKind = Literal[
    "account_event",
    "arm",
    "backend_warning",
    "bank_partition",
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
    "launch_clock",
    "launch_released",
    "llm_call_progress",
    "llm_call",
    "llm_call_start",
    "optimizer_state",
    "phase",
    "priced_key",
    "provider_eased",
    "provider_hold",
    "race_catch_up",
    "race_standing",
    "resolved_run",
    "round_closed",
    "round_entered",
    "round_proposed",
    "round_warning",
    "run_phase",
    "run_wiring",
    "sample_scored",
    "sample_started",
    "scoring_locked",
    "spawned",
    "spend_hold",
    "spend_tombstone",
    "standing_budget",
    "token_usage",
    "trail_tombstone",
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
    arm.model_fields["record_type"].default for arm in get_args(SilentRecord)
)

del _record_types, _declared

_ACTIVITY_ADAPTER: TypeAdapter[ActivityRecord] = TypeAdapter(
    Annotated[ActivityRecord, Field(discriminator="record_type")],
    config=ConfigDict(defer_build=True),
)


def bearing_record(kind: str, rec: dict[str, Any]) -> ActivityRecord | None:
    """``None`` for a kind that never renders, and for a line no arm accepts (``CycleEventLog.iter``'s skip)."""
    if kind in NON_ACTIVITY_KINDS:
        return None
    try:
        return _ACTIVITY_ADAPTER.validate_python(rec)
    except ValidationError:
        return None


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
