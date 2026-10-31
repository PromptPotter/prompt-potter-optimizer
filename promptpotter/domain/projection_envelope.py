from __future__ import annotations

from typing import Any, Literal, TypeIs, get_args

from pydantic import Field

from promptpotter.domain.activity import ActivityRecord, ActivityState
from promptpotter.domain.run_records import RECORD_KINDS, CycleRecord
from promptpotter.domain.strict_model import FrozenModel

# A missing kind is a HOLE, not a filter: the tail still consumes its offset, and the client reconnects.
ProjectionKind = Literal[
    "account_event",
    "arm",
    "backend_warning",
    "bank_partition",
    "campaign_ceiling",
    "decision",
    "command",
    "command_ack",
    "cycle_final",
    "cycle_minted",
    "cycle_seed",
    "cycle_share",
    "cycle_superseded",
    "election",
    "error",
    "flight",
    "fork_graded",
    "health",
    "intervention",
    "launch_claim",
    "launch_clock",
    "launch_released",
    "left_comparison",
    "llm_call_progress",
    "llm_call",
    "llm_call_start",
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
    "round_restated",
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
_declared = frozenset(get_args(ProjectionKind)) - _PROJECTION_ONLY
if _declared != RECORD_KINDS:
    raise RuntimeError(
        "ProjectionKind must cover the CycleRecord union exactly — "
        f"missing {sorted(RECORD_KINDS - _declared)}, "
        f"unbacked {sorted(_declared - RECORD_KINDS)}."
    )
del _declared

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
