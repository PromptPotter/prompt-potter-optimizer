from __future__ import annotations

from pydantic import Field

from promptpotter.domain.activity import ActivityItem
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.strict_model import FrozenModel

# A time-ray window's items: what one request is served, and the most a store's ray fold keeps.
DEFAULT_RAY_LIMIT = 200
MAX_RAY_LIMIT = 1000


class RayItem(FrozenModel):
    """One ray event, addressed by ``path``: a bare ``cycle_id`` is ambiguous in an L4 family."""

    path: list[CycleHop] = Field(
        description="The cycle this record belongs to, root → leaf — THE address.",
    )
    offset: int = Field(
        ge=0,
        description="Physical 0-based line index in this cycle's own ledger — the same space "
        "as ProjectionEnvelope.sequence, so a live SSE frame de-duplicates against a ray "
        "item on (path, offset). SPARSE: a record no line is made of rides nowhere, so "
        "consecutive items skip offsets; a gap between ray offsets is not a missing record.",
    )
    ts: str = Field(
        description="Effective timestamp: the record's own, raised to its file predecessor's "
        "when the two invert (records are stamped at construction but appended later).",
    )
    gap_before_s: float = Field(
        ge=0,
        description="Seconds since the item before it in this window; 0 on the window's first.",
    )
    activity: ActivityItem | None = Field(
        description="The record's reading — its line, and the round and candidate it is about. "
        "Null on a bare heartbeat alone: the process proved alive and there is nothing to read, "
        "so it ends a silence without becoming a step. A `running` item with no later reading "
        "on its path is a call still open.",
    )


class RayResponse(FrozenModel):
    """One ordered window of a family's chronology, oldest-first."""

    items: list[RayItem] = Field(
        default_factory=list,
        description="The window, oldest-first: every record a line was read off, and each bare "
        "heartbeat between them.",
    )
    cursor_prev: str | None = Field(
        default=None,
        description="Opaque cursor for the window immediately older than this one; null when "
        "this window already reaches the family's beginning.",
    )
