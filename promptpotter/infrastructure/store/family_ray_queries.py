"""**Never read through ``CycleEventLog.iter``**: a fork's replays the prefix the ray already read."""

from __future__ import annotations

import base64
import hashlib
import json
from collections import deque
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

from pydantic import ConfigDict, Field

from promptpotter.domain import activity
from promptpotter.domain.activity import ActivityFeed, ActivityItem
from promptpotter.domain.cycle_paths import CycleHop, CyclePath, encode_cycle_path
from promptpotter.domain.projection_envelope import RECORD_KINDS, bearing_record
from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    course_validator_ns,
    cycle_dir_for,
)
from promptpotter.infrastructure.store.lineage_queries import FamilyCourse
from promptpotter.infrastructure.store.read_model import LedgerIndex
from promptpotter.shared.clock import epoch_seconds

__all__ = [
    "DEFAULT_RAY_LIMIT",
    "MAX_RAY_LIMIT",
    "RayItem",
    "RayResponse",
    "build_family_ray",
    "decode_ray_cursor",
    "ray_validator_parts",
]

DEFAULT_RAY_LIMIT = 200
MAX_RAY_LIMIT = 1000

# `encoded_path`, not the walk's rank: a rank is request-relative, so cursors would not compare.
RayCursor = tuple[float, str, int]

_INNER_KINDS: frozenset[str] = frozenset({"cycle_seed", "round_standing", "round_warning", "error"})

assert _INNER_KINDS <= RECORD_KINDS, (
    f"curation names unknown record kinds: {sorted(_INNER_KINDS - RECORD_KINDS)}"
)

# A validator input that moves on DEPLOY, not on a write: left out, a changed one 304s forever.
_CURATION_TAG = (
    sorted(_INNER_KINDS),
    hashlib.sha256(Path(activity.__file__).read_bytes()).hexdigest(),
)


class RayItem(StrictModel):
    """One ray event, addressed by ``path``: a bare ``cycle_id`` is ambiguous in an L4 family."""

    model_config = ConfigDict(frozen=True)

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


class RayResponse(StrictModel):
    """One ordered window of a family's chronology, oldest-first."""

    model_config = ConfigDict(frozen=True)

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


class _Raw(NamedTuple):
    offset: int
    epoch: float
    ts: str
    activity: ActivityItem | None


class _Tail(NamedTuple):
    rows: list[_Raw]
    total: int


class _RayTail:
    probes: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self, limit: int = MAX_RAY_LIMIT, path_and_bound: tuple[str, RayCursor] | None = None
    ) -> None:
        self._below = path_and_bound
        # Fed every bearing record, kept or not: one record declares the round of those after it.
        self._feed = ActivityFeed()
        self._own: deque[_Raw] = deque(maxlen=limit)
        self._inner: deque[_Raw] = deque(maxlen=limit)
        self._own_total = 0
        self._inner_total = 0
        self._last_epoch: float | None = None
        self._last_ts = ""

    def feed(self, offset: int, rec: dict[str, Any]) -> None:
        kind = rec.get("record_type")
        if not isinstance(kind, str) or kind not in RECORD_KINDS:
            return
        record = bearing_record(kind, rec)
        item = None if record is None else self._feed.read(offset, record)
        own = epoch_seconds(rec.get("timestamp"))
        if own is None or (self._last_epoch is not None and own < self._last_epoch):
            if self._last_epoch is None:
                # A fabricated epoch would sort below every outstanding cursor and mutate served windows.
                return
        else:
            self._last_epoch, self._last_ts = own, str(rec.get("timestamp"))
        # A bare heartbeat rides unread: it is what ends a silence on the ray.
        if item is None and not isinstance(record, LLMCallProgressRecord):
            return
        assert self._last_epoch is not None
        if self._below and (self._last_epoch, self._below[0], offset) >= self._below[1]:
            return
        row = _Raw(offset, self._last_epoch, self._last_ts, item)
        self._own.append(row)
        self._own_total += 1
        if kind in _INNER_KINDS:
            self._inner.append(row)
            self._inner_total += 1

    def value(self) -> tuple[_Tail, _Tail]:
        return (
            _Tail(list(self._own), self._own_total),
            _Tail(list(self._inner), self._inner_total),
        )


_RAY_FOLDS = (_RayTail,)


def _read_curated(
    ledger: Path, *, encoded_path: str, depth: int, keep: int, bound: RayCursor | None
) -> tuple[list[_Raw], bool]:
    tail = LedgerIndex.of(ledger, _RAY_FOLDS).view(_RayTail)[min(depth, 1)]
    if bound is not None:
        below = [r for r in tail.rows if (r.epoch, encoded_path, r.offset) < bound]
        if len(below) < keep and tail.total > len(tail.rows):
            window = partial(_RayTail, keep, (encoded_path, bound))
            older_tail = LedgerIndex(ledger, (window,)).view(_RayTail)[min(depth, 1)]
            return older_tail.rows, older_tail.total > keep
        older = tail.total - len(tail.rows) + len(below)
        return below[-keep:], older > keep
    return tail.rows[-keep:], tail.total > keep


def encode_ray_cursor(key: RayCursor) -> str:
    blob = json.dumps(list(key), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(blob).decode()


def decode_ray_cursor(raw: str | None) -> RayCursor | None:
    """Deliberately intolerant: a mangled cursor re-serves or skips a window."""
    if not raw:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
    except ValueError as exc:
        raise ValueError(f"Malformed ray cursor: {exc}") from exc
    if (
        not isinstance(data, list)
        or len(data) != 3
        or isinstance(data[0], bool)
        or not isinstance(data[0], int | float)
        or not isinstance(data[1], str)
        or isinstance(data[2], bool)
        or not isinstance(data[2], int)
        or data[2] < 0
    ):
        raise ValueError("Malformed ray cursor: expected [epoch, path, offset]")
    return (float(data[0]), data[1], data[2])


def ray_validator_parts(
    courses: list[FamilyCourse], *, limit: int, before: str | None
) -> tuple[object, ...]:
    """Deep windows revalidate like the head: immutability would bet against a backdated append."""
    parts: list[object] = ["ray", _CURATION_TAG, limit, before]
    for course in courses:
        parts.append(encode_cycle_path(course.path))
        parts.append(course_validator_ns(cycle_dir_for(course.store.base_dir, course.path[-1])))
    return tuple(parts)


def build_family_ray(
    courses: list[FamilyCourse], *, limit: int, before: RayCursor | None
) -> RayResponse:
    pool: list[tuple[RayCursor, CyclePath, _Raw]] = []
    truncated = False

    for course in courses:
        ledger = CycleLayout(cycle_dir_for(course.store.base_dir, course.path[-1])).ledger
        key = encode_cycle_path(course.path)
        rows, dropped = _read_curated(
            ledger, encoded_path=key, depth=course.depth, keep=limit, bound=before
        )
        truncated = truncated or dropped
        for row in rows:
            pool.append(((row.epoch, key, row.offset), course.path, row))

    pool.sort(key=lambda entry: entry[0])
    if len(pool) > limit:
        pool = pool[-limit:]
        truncated = True

    epochs = [row.epoch for _key, _path, row in pool]
    items = [
        RayItem(
            path=list(path),
            offset=row.offset,
            ts=row.ts,
            gap_before_s=gap,
            activity=row.activity,
        )
        for (_key, path, row), gap in zip(
            pool, [0.0, *(b - a for a, b in pairwise(epochs))], strict=True
        )
    ]
    return RayResponse(
        items=items,
        cursor_prev=encode_ray_cursor(pool[0][0]) if truncated and pool else None,
    )
