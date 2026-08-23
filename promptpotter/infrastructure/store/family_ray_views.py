"""The time-ray — one chronology merging a course's own ledgers with its forks' and inner runs'.
**Never read through ``CycleEventLog.iter``**: a fork's replays the prefix the ray already read."""

from __future__ import annotations

import base64
import json
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, cast, get_args

from pydantic import ConfigDict, Field

from promptpotter.domain.cycle_paths import CycleHop, CyclePath, encode_cycle_path
from promptpotter.domain.projection_envelope import NON_ACTIVITY_KINDS, ProjectionKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.layout import (
    CycleLayout,
    course_validator_ns,
    cycle_dir_for,
)
from promptpotter.infrastructure.store.lineage_views import FamilyCourse

logger = logging.getLogger(__name__)

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

# The merge/cursor key. Strictly increasing within one file (the clamp makes the epoch
# non-decreasing, the offset breaks ties), totally ordered across the family (no two
# records share path+offset). `encoded_path`, not the walk's rank: the rank is
# request-relative — a course discovered between two requests shifts every deeper rank —
# so a rank baked into a cursor would not be comparable across requests.
RayCursor = tuple[float, str, int]

_VALID_KINDS: frozenset[str] = frozenset(get_args(ProjectionKind))

# Dropped at EVERY depth: a kind no feed item is ever made of, so serving one is bytes the
# renderer can only throw away. DERIVED from the single declaration rather than restated — this
# was a hand-typed pair whose comment claimed to mirror the client's translator "exactly", and
# the client had long since drifted to six. Payload-inspecting drops (a `snapshot` that is a
# `p_best_update`) are the renderer's and stay there. Depth >= 1 is a different regime — a
# deliberate server-side milestone cut (see `_INNER_KINDS`).
_NEVER_KINDS: frozenset[str] = NON_ACTIVITY_KINDS

# At depth >= 1 (an inner run) only milestones ride: an L4 outer round runs a panel of
# whole inner campaigns, and their full ledgers would bury the outer story. `phase` covers
# `round:*`, the `control:*` run-phase declarations, and `backend` warnings. No
# `llm_call_progress`: while an inner campaign runs, the OUTER cycle emits its own
# heartbeat, so the same wall-clock is already proven alive by a record the ray keeps.
_INNER_KINDS: frozenset[str] = frozenset({"cycle_seed", "round_warning", "error"})
_INNER_PHASES: frozenset[str] = frozenset({"round", "control", "backend"})

# A typo in a hand-typed kind silently never matches — fail at import instead. `_NEVER_KINDS`
# needs no such guard now: it is keyed off `ProjectionKind` itself.
assert _INNER_KINDS <= _VALID_KINDS, (
    f"curation names unknown ProjectionKinds: {sorted(_INNER_KINDS - _VALID_KINDS)}"
)

# The curation is a validator input too — it decides the body, and it is the one input that
# moves on DEPLOY rather than on a write. Left out, a changed drop set 304s every client into
# the body it was served before, forever on a campaign whose ledger will never move again.
_CURATION_TAG = (sorted(_NEVER_KINDS), sorted(_INNER_KINDS), sorted(_INNER_PHASES))


class RayItem(StrictModel):
    """One event on the ray: a projection envelope plus its address.

    ``kind``/``payload`` are byte-identical to ``ProjectionEnvelope``'s, so the webapp's
    ``projectionToActivity`` maps a ``RayItem`` unchanged. ``path`` is the address — a bare
    ``cycle_id`` is ambiguous inside an L4 family (inner ids repeat across sandboxes).
    """

    model_config = ConfigDict(frozen=True)

    path: list[CycleHop] = Field(
        description="The cycle this record belongs to, root → leaf — THE address.",
    )
    offset: int = Field(
        ge=0,
        description="Physical 0-based line index in this cycle's own ledger — the same space "
        "as ProjectionEnvelope.sequence, so a live SSE frame de-duplicates against a ray "
        "item on (path, offset). SPARSE: server curation drops kinds, so consecutive items "
        "may skip offsets; a gap between ray offsets is not a missing record.",
    )
    ts: str = Field(
        description="Effective timestamp: the record's own, raised to its file predecessor's "
        "when the two invert (records are stamped at construction but appended later).",
    )
    kind: ProjectionKind = Field(description="The ledger record_type — ProjectionEnvelope.kind.")
    payload: dict[str, Any] = Field(
        default_factory=dict, description="The record's model_dump — ProjectionEnvelope.payload."
    )


class RayResponse(StrictModel):
    """One ordered window of a family's chronology, oldest-first."""

    model_config = ConfigDict(frozen=True)

    items: list[RayItem] = Field(
        default_factory=list,
        description="The window, oldest-first. Includes llm_call_progress heartbeats — the "
        "client proves liveness across a silent stretch with them before dropping them from "
        "the rendered steps.",
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
    kind: str
    payload: dict[str, Any]


def _epoch(raw: object) -> float | None:
    """A record timestamp as a sortable instant. Parsed, never string-compared — ``utcnow_iso`` omits
    the fractional part at exactly zero microseconds, which sorts AFTER ``...T12:00:00.5Z``."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (ValueError, OSError, OverflowError):
        # `.timestamp()` raises OSError/OverflowError (not ValueError) for out-of-range
        # datetimes on Windows; one corrupt line must not 500 the whole ray.
        return None


def _curated(kind: str, rec: dict[str, Any], *, depth: int) -> bool:
    if kind in _NEVER_KINDS:
        return False
    if depth == 0:
        return True
    if kind in _INNER_KINDS:
        return True
    return kind == "phase" and rec.get("phase") in _INNER_PHASES


def _read_curated(
    ledger: Path, *, encoded_path: str, depth: int, keep: int, bound: RayCursor | None
) -> tuple[list[_Raw], bool]:
    """The newest ``keep`` curated records below ``bound``, oldest-first, plus whether older ones fell
    out. Clamped over the WHOLE file: records are stamped at construction but appended later."""
    if not ledger.is_file():
        return [], False
    window: deque[_Raw] = deque(maxlen=keep)
    dropped = False
    last_epoch: float | None = None
    last_ts = ""
    clamped = 0
    with ledger.open("rb") as fh:
        # `enumerate` counts every physical line, exactly as `CycleEventLog.append` assigns
        # offsets — these indices join against a live SSE frame's `sequence`. Do not
        # skip-without-counting.
        for offset, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                # A torn final line (append is not crash-atomic) or a version-skewed record.
                # Skip-and-continue, as every sibling reader does — one bad line must not
                # blind the whole chronology.
                logger.warning(
                    "skipping unparseable ledger line at offset %d in %s", offset, ledger
                )
                continue
            if not isinstance(rec, dict):
                continue
            kind = rec.get("record_type")
            if not isinstance(kind, str) or kind not in _VALID_KINDS:
                continue

            own = _epoch(rec.get("timestamp"))
            if own is None:
                if last_epoch is None:
                    # No usable time yet. A fabricated epoch would sort below every
                    # outstanding cursor and mutate windows that were already served.
                    logger.warning(
                        "no parseable timestamp yet at offset %d in %s — skipped", offset, ledger
                    )
                    continue
                eff_epoch, eff_ts = last_epoch, last_ts
            elif last_epoch is not None and own < last_epoch:
                eff_epoch, eff_ts = last_epoch, last_ts
                clamped += 1
            else:
                eff_epoch, eff_ts = own, str(rec.get("timestamp"))
            last_epoch, last_ts = eff_epoch, eff_ts

            if bound is not None and (eff_epoch, encoded_path, offset) >= bound:
                break
            if not _curated(kind, rec, depth=depth):
                continue
            if len(window) == keep:
                dropped = True
            window.append(_Raw(offset, eff_epoch, eff_ts, kind, rec))
    if clamped:
        logger.warning("clamped %d inverted timestamp(s) in %s", clamped, ledger)
    return list(window), dropped


def encode_ray_cursor(key: RayCursor) -> str:
    blob = json.dumps(list(key), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(blob).decode()


def decode_ray_cursor(raw: str | None) -> RayCursor | None:
    """Parse a cursor; ``ValueError`` on a malformed token, which the route maps to 400. Deliberately
    intolerant — a mangled cursor re-serves or skips a window, and a chronology with a hole is worse."""
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
    """Everything the body depends on: the curation, the query, and each course's own freshness.
    Deep windows revalidate exactly like the head — claiming immutability would bet against a
    backdated append."""
    parts: list[object] = ["ray", _CURATION_TAG, limit, before]
    for course in courses:
        parts.append(encode_cycle_path(course.path))
        parts.append(course_validator_ns(cycle_dir_for(course.store.base_dir, course.path[-1])))
    return tuple(parts)


def build_family_ray(
    courses: list[FamilyCourse], *, limit: int, before: RayCursor | None
) -> RayResponse:
    """Merge the family's ledgers into one ordered window, oldest-first. ``FamilyCourse.depth`` puts a
    FORK at depth 0 beside its parent — a chronology wants it interleaved, not indented under it."""
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

    items = [
        RayItem(
            path=list(path),
            offset=row.offset,
            ts=row.ts,
            kind=cast(ProjectionKind, row.kind),
            payload=row.payload,
        )
        for _key, path, row in pool
    ]
    return RayResponse(
        items=items,
        cursor_prev=encode_ray_cursor(pool[0][0]) if truncated and pool else None,
    )
