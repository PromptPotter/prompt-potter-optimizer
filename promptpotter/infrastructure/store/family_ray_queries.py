"""**Never read through ``CycleEventLog.iter``**: a fork's replays the prefix the ray already read."""

from __future__ import annotations

import base64
import hashlib
import json
from collections import deque
from functools import partial
from itertools import pairwise
from pathlib import Path
from typing import Any, NamedTuple

from promptpotter.domain import activity
from promptpotter.domain.activity import ActivityFeed, ActivityItem
from promptpotter.domain.cycle_paths import CyclePath, encode_cycle_path, step_address
from promptpotter.domain.hierarchy import MAX_STEP_LIMIT, HierarchyStep, HierarchySteps, StepFacts
from promptpotter.domain.projection_envelope import RECORD_KINDS, bearing_record
from promptpotter.domain.run_records import LLMCallProgressRecord
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    course_validator_ns,
    cycle_dir_for,
)
from promptpotter.infrastructure.store.lineage_queries import FamilyCourse
from promptpotter.infrastructure.store.read_model import LineIndex
from promptpotter.shared.clock import epoch_seconds

# `encoded_path`, not the walk's rank: a rank is request-relative, so cursors would not compare.
RayCursor = tuple[float, str, int]

_INNER_KINDS: frozenset[str] = frozenset({"cycle_seed", "round_closed", "round_warning", "error"})

assert _INNER_KINDS <= RECORD_KINDS, (
    f"curation names unknown record kinds: {sorted(_INNER_KINDS - RECORD_KINDS)}"
)

# A validator input that moves on DEPLOY, not on a write: left out, a changed one 304s forever.
_CURATION_TAG = (
    sorted(_INNER_KINDS),
    hashlib.sha256(Path(activity.__file__).read_bytes()).hexdigest(),
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
    def __init__(
        self, limit: int = MAX_STEP_LIMIT, path_and_bound: tuple[str, RayCursor] | None = None
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

    def feed(self, offset: int, line: dict[str, Any]) -> None:
        kind = line.get("record_type")
        if not isinstance(kind, str) or kind not in RECORD_KINDS:
            return
        record = bearing_record(kind, line)
        item = None if record is None else self._feed.read(offset, record)
        own = epoch_seconds(line.get("timestamp"))
        if own is None or (self._last_epoch is not None and own < self._last_epoch):
            if self._last_epoch is None:
                # A fabricated epoch would sort below every outstanding cursor and mutate served windows.
                return
        else:
            self._last_epoch, self._last_ts = own, str(line.get("timestamp"))
        # A bare heartbeat is kept to end a silence, then served as no step.
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
    tail = LineIndex.of(ledger, _RAY_FOLDS).view(_RayTail)[min(depth, 1)]
    if bound is not None:
        below = [r for r in tail.rows if (r.epoch, encoded_path, r.offset) < bound]
        if len(below) < keep and tail.total > len(tail.rows):
            window = partial(_RayTail, keep, (encoded_path, bound))
            older_tail = LineIndex(ledger, (window,)).view(_RayTail)[min(depth, 1)]
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
) -> HierarchySteps:
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
    steps = [
        HierarchyStep(
            address=step_address(key, row.offset),
            parent=key,
            label=row.activity.label,
            lede=None,
            caveat=None,
            tags=[],
            stats=[],
            children=[],
            facts=StepFacts(offset=row.offset, ts=row.ts, gap_before_s=gap, activity=row.activity),
        )
        for ((_epoch, key, _offset), _path, row), gap in zip(
            pool, [0.0, *(b - a for a, b in pairwise(epochs))], strict=True
        )
        if row.activity is not None
    ]
    return HierarchySteps(
        steps=steps,
        cursor_prev=encode_ray_cursor(pool[0][0]) if truncated and pool else None,
    )
