"""The time-ray — one chronology across a course and everything below it.

Every other served axis is round-indexed (``/rounds``), tree-structured (``/tree``), or a
live edge with no history (the SSE tail seeks to EOF). The ray is the missing object — the
ledgers of a course, its forks and its inner runs, merged into one ordered sequence — and it
is the **replay endpoint** ``domain/projection_envelope.py`` names: a subscriber heals a
sequence gap here, never via a ``since=`` on the tail.

**Never read through** :meth:`CycleEventLog.iter` — a fork's ``iter()`` virtually replays
the parent's prefix, and the ray reads the parent too, so every parent record would appear
twice. This module reads each ledger's own file, as ``scan_ledger_candidates`` and
``CycleLedgerTail`` do (the two-offset-spaces note on
``infrastructure/ledger.py::CycleEventLog.iter``).

**Windowing.** Records merge on one key — ``(ts_eff, encoded_path, offset)`` — and the
cursor IS that key, taken from the oldest item a window returned: a deeper page is every
record strictly below it, so consecutive windows partition the key space (no overlap, no
hole). A cycle discovered after the head fetch (a new fork) has only keys above every
outstanding cursor, so it enters at the next head fetch instead of corrupting a deep page.

**Cost.** One forward pass per family ledger, retaining at most ``limit`` records per file.
The pass parses every line: the monotonic timestamp clamp needs each record in file order,
and a record's kind is only knowable after parsing. The ETag keeps that off the poll path.
"""

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
from promptpotter.domain.projection_envelope import ProjectionKind
from promptpotter.domain.strict_model import StrictModel
from promptpotter.infrastructure.store.io import newest_mtime_ns
from promptpotter.infrastructure.store.layout import CycleLayout, cycle_dir_for
from promptpotter.infrastructure.store.lineage_views import FamilyCourse

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RAY_LIMIT",
    "MAX_RAY_LIMIT",
    "RayCursor",
    "RayItem",
    "RayResponse",
    "build_family_ray",
    "decode_ray_cursor",
    "encode_ray_cursor",
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

# Dropped at EVERY depth — exactly the kinds the client's translator
# (`webapp/lib/chat/activity.ts::projectionToActivity`) drops unconditionally BY KIND. At
# depth 0 this filter must stay strictly COARSER than the client's: payload-inspecting
# drops (a `snapshot` that is a `p_best_update`) stay the client's call, or the server
# silently narrows what the ray can ever show. Depth >= 1 is a different regime — a
# deliberate server-side milestone cut, finer than the client (see `_INNER_KINDS`).
_NEVER_KINDS: frozenset[str] = frozenset({"token_usage", "decision"})

# At depth >= 1 (an inner run) only milestones ride: an L4 outer round runs a panel of
# whole inner campaigns, and their full ledgers would bury the outer story. `phase` covers
# `round:*`, the `control:*` run-phase declarations, and `backend` warnings. No
# `llm_call_progress`: while an inner campaign runs, the OUTER cycle emits its own
# heartbeat, so the same wall-clock is already proven alive by a record the ray keeps.
_INNER_KINDS: frozenset[str] = frozenset({"cycle_seed", "round_warning", "error"})
_INNER_PHASES: frozenset[str] = frozenset({"round", "control", "backend"})

# A typo in a hand-typed kind silently never matches — fail at import instead.
assert _NEVER_KINDS <= _VALID_KINDS and _INNER_KINDS <= _VALID_KINDS, (
    f"curation names unknown ProjectionKinds: {sorted((_NEVER_KINDS | _INNER_KINDS) - _VALID_KINDS)}"
)


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
    """A record timestamp as a sortable instant; ``None`` if absent or unparseable.

    Parsed, not string-compared: ``utcnow_iso`` omits the fractional part when microseconds
    are exactly zero, so ``...T12:00:00Z`` sorts *after* ``...T12:00:00.5Z`` lexically.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except (ValueError, OSError, OverflowError):
        # `.timestamp()` raises OSError/OverflowError (not ValueError) for out-of-range
        # datetimes on Windows; one corrupt line must not 500 the whole ray.
        return None


def _curated(kind: str, rec: dict[str, Any], *, depth: int) -> bool:
    """Does this record ride the ray? See ``_NEVER_KINDS`` / ``_INNER_KINDS``."""
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
    """The newest ``keep`` curated records whose merge key is below ``bound``, oldest-first.

    Returns ``(rows, dropped)`` — ``dropped`` says older curated records fell out of the
    window, i.e. this file still has history to page back into.

    **The clamp runs over the whole file, not the window.** ``ts_eff = max(own ts, previous
    ts_eff)``, carried in FILE order: records are stamped at construction but appended
    later, so a raw-timestamp sort can place a record *before* the record that caused it,
    and append order is the one authority a single file has. Starting the carry mid-file
    would repair against a skipped record, so the pass reads from the top; the merge key
    strictly increases within a file, so once it reaches ``bound`` the rest of the file is
    above it and the loop stops.
    """
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
    """The merge key of a window's oldest item, as an opaque token."""
    blob = json.dumps(list(key), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(blob).decode()


def decode_ray_cursor(raw: str | None) -> RayCursor | None:
    """Parse a cursor; raises ``ValueError`` on a malformed token — the route maps it to 400.

    Deliberately not tolerant: a cursor the client mangled would silently re-serve or skip
    a window, and a chronology with a hole in it is worse than an error.
    """
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
    """Everything the response body depends on: the query, and each course's ledger + index
    mtimes.

    Deep windows revalidate exactly like the head. By the cursor's key argument an append
    lands above every outstanding cursor — but claiming byte-immutability would bet against
    a backdated append, which the clamp bounds only within its own file. Revalidation is
    cheap; a wrong 304 is silent.
    """
    parts: list[object] = ["ray", limit, before]
    for course in courses:
        layout = CycleLayout(cycle_dir_for(course.store.base_dir, course.path[-1]))
        parts.append(encode_cycle_path(course.path))
        parts.append(newest_mtime_ns(layout.ledger, layout.manifest))
    return tuple(parts)


def build_family_ray(
    courses: list[FamilyCourse], *, limit: int, before: RayCursor | None
) -> RayResponse:
    """Merge the family's ledgers into one ordered window, oldest-first.

    The sort key is the merge key (see :data:`RayCursor`): two records in different cycles
    inside the clock's resolution are ordered arbitrarily-but-stably by path — the honest
    limit of one machine's clock. A distributed runner would need a family-scoped Lamport
    counter; that does not exist and must not be faked.

    ``FamilyCourse.depth`` is hop count off the family root — a FORK is depth 0 beside its
    parent (it replaces the leaf hop rather than extending the path) while an inner run is
    depth 1. Right for a chronology: a fork is the same campaign continuing on another
    branch, and seeing it interleaved with the parent is the point.
    """
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
