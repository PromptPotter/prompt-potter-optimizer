"""The closed outbound SSE set declared in ``docs/specs/m12-events-asyncapi.yaml``. ``sequence`` is the ledger
offset — a subscriber detects gaps by it and replays from the family ray, never via a ``since=`` on the tail."""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.run_records import CycleRecord
from promptpotter.domain.strict_model import StrictModel

__all__ = [
    "NON_ACTIVITY_KINDS",
    "RENDERS_AS_ACTIVITY",
    "ProjectionEnvelope",
    "ProjectionKind",
]


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

# Whether a kind can EVER become an item in the activity feed — the ONE declaration of the
# feed's vocabulary, which is why the ray needs none of its own. ``False`` is the licence not to
# serve the record at all (``store/family_ray_views.py``); ``True`` still renders conditionally
# on the payload — a ``snapshot`` that is a ``p_best_update`` yields nothing — and THAT decision
# belongs to the renderer. Total over the RECORD kinds: ``stream_snapshot`` is synthesized by
# the tail, reaches its own translator, and is on no ledger for the ray to filter.
RENDERS_AS_ACTIVITY: dict[ProjectionKind, bool] = {
    "candidate_minted": True,
    "command": True,
    "command_ack": True,
    "cycle_seed": True,
    "decision": False,
    "election": False,
    "error": True,
    "llm_call": True,
    "llm_call_progress": True,
    "llm_call_start": True,
    "phase": True,
    "round_warning": True,
    "ruler": False,
    "snapshot": True,
    "spend_tombstone": False,
    "token_usage": False,
}

if frozenset(RENDERS_AS_ACTIVITY) != _declared:
    raise RuntimeError(
        "RENDERS_AS_ACTIVITY must answer for every record kind — an unanswered one is a record "
        "the ray cannot decide about and the feed drops in silence: "
        f"missing {sorted(_declared - frozenset(RENDERS_AS_ACTIVITY))}, "
        f"unbacked {sorted(frozenset(RENDERS_AS_ACTIVITY) - _declared)}."
    )

#: The complement, as both consumers want it: the ray's drop set and the client's proof that
#: nothing reaching its default arm was ever meant to render.
NON_ACTIVITY_KINDS: frozenset[ProjectionKind] = frozenset(
    kind for kind, renders in RENDERS_AS_ACTIVITY.items() if not renders
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
