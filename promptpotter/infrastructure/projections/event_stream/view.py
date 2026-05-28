"""``EventStreamView`` — sole writer of outbound SSE projection frames.

Per-cycle ledger subscriber. ``on_record`` is invoked synchronously from
``CycleEventLog.append``; the view fans every record out to an in-process
set of ``EventStreamSubscriber`` queues. Each subscriber bridges from the
ledger-thread to the SSE handler's asyncio loop via
``loop.call_soon_threadsafe``.

**Sole-writer rule (security box 4).** This module is the ONLY writer of
SSE frames. Other projections (``LiveDashboardView``, ``AuditTrailView``,
``PoBBStreamView``) write their own on-disk artifacts; only
``EventStreamView`` emits envelopes onto the live HTTP fan-out.

**Snapshot-then-tail (security box 14).** A new subscriber atomically
captures the ledger's ``next_offset`` at attach time. The SSE handler
then reads ``dashboard.json`` and emits one ``stream_snapshot`` envelope
with ``sequence`` = captured offset. Subsequent records (offsets
> captured) arrive via the live tail. Clients de-dupe / detect gaps by
sequence.

**Heartbeat (security box 15).** The SSE handler emits an SSE comment
line every 15 s when the subscriber's queue is idle so dumb proxies don't
idle-close the connection.

**Backpressure.** Queue is bounded (default 1024 frames). On overflow the
subscriber is closed and clients reconnect; the snapshot at the new
``next_offset`` covers everything they missed.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from promptpotter.domain.cycle_paths import CycleDir
from promptpotter.domain.projection_envelope import ProjectionEnvelope, ProjectionKind
from promptpotter.domain.run_records import CycleRecord

logger = logging.getLogger(__name__)


__all__ = ["EventStreamSubscriber", "EventStreamView"]


_DEFAULT_QUEUE_SIZE = 1024
_OnCloseCallback = Callable[[int], None]


class EventStreamSubscriber:
    """One HTTP subscriber's bridge from the ledger thread to its SSE handler loop."""

    def __init__(
        self,
        sub_id: int,
        start_offset: int,
        *,
        on_close: _OnCloseCallback,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._id = sub_id
        self._start_offset = start_offset
        self._on_close = on_close
        self._queue: asyncio.Queue[ProjectionEnvelope] = asyncio.Queue(maxsize=queue_size)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._lock = threading.Lock()

    @property
    def sub_id(self) -> int:
        return self._id

    @property
    def start_offset(self) -> int:
        """Ledger offset at subscribe time. Snapshot frame carries this as ``sequence``."""
        return self._start_offset

    @property
    def closed(self) -> bool:
        return self._closed

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the SSE handler's asyncio loop. Must be called before any ``publish``
        reaches this subscriber from the ledger thread."""
        with self._lock:
            self._loop = loop

    def publish(self, envelope: ProjectionEnvelope) -> None:
        """Called sync from the ledger thread; hops to the asyncio loop via
        ``call_soon_threadsafe``. Drops + closes the subscriber on overflow."""
        with self._lock:
            loop = self._loop
            if self._closed or loop is None:
                return
        try:
            loop.call_soon_threadsafe(self._enqueue, envelope)
        except RuntimeError:
            # Loop closed underneath us
            self.close()

    def _enqueue(self, envelope: ProjectionEnvelope) -> None:
        """Runs on the asyncio loop; non-blocking put with overflow → close."""
        if self._closed:
            return
        try:
            self._queue.put_nowait(envelope)
        except asyncio.QueueFull:
            logger.warning("EventStreamSubscriber %d queue full; closing", self._id)
            self.close()

    async def stream(
        self, *, heartbeat_interval_s: float = 15.0
    ) -> AsyncIterator[ProjectionEnvelope | None]:
        """Yield envelopes; yield ``None`` once per ``heartbeat_interval_s`` of idle.

        The handler converts ``None`` to an SSE comment line. Wakes
        promptly when ``close()`` fires.
        """
        while not self._closed:
            try:
                envelope = await asyncio.wait_for(self._queue.get(), heartbeat_interval_s)
            except TimeoutError:
                if self._closed:
                    return
                yield None
                continue
            yield envelope

    def close(self) -> None:
        """Idempotent. After ``close``, ``publish`` is a no-op and ``stream`` exits."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._on_close(self._id)
        except Exception:
            logger.exception("EventStreamSubscriber on_close failed for sub_id=%d", self._id)


class EventStreamView:
    """Per-cycle ledger subscriber + SSE fan-out.

    Wire shape contract: every frame is a
    :class:`~promptpotter.domain.projection_envelope.ProjectionEnvelope`
    with ``kind`` = the underlying record's ``record_type`` (live tail) or
    one of the projection-only kinds (``stream_snapshot``, ``command``,
    ``command_ack``).
    """

    def __init__(self, cycle_dir: CycleDir, *, cycle_id: str, initial_offset: int = 0) -> None:
        self._cycle_dir = Path(cycle_dir)
        self._cycle_id = cycle_id
        self._subscribers: dict[int, EventStreamSubscriber] = {}
        self._sub_ids = itertools.count()
        self._lock = threading.Lock()
        # Tracks the next offset a record would receive — initially the
        # ledger's ``next_offset`` at bind time (so resumed cycles attach
        # subscribers past their existing on-disk record history), then
        # advanced on every ``on_record``.
        self._next_offset = initial_offset

    @property
    def cycle_id(self) -> str:
        return self._cycle_id

    @property
    def cycle_dir(self) -> Path:
        return self._cycle_dir

    @property
    def next_offset(self) -> int:
        """Highest offset broadcast + 1. Subscribers attach at this offset."""
        return self._next_offset

    # ---- Ledger subscriber interface ----

    def on_record(self, record: CycleRecord, offset: int) -> None:
        """Sole writer of outbound SSE frames for this cycle. Called sync
        from ``CycleEventLog.append``.

        ``model_dump(mode="json")`` MUST succeed — any live runtime references
        in record payloads are stripped upstream (``RunCallbacks.on_phase``
        drops ``env=session`` and similar non-serializable keys before
        building the ``PhaseRecord``). A serialization failure here is a
        contract violation, not a recoverable case.
        """
        kind: ProjectionKind = record.record_type
        payload = record.model_dump(mode="json")
        envelope = ProjectionEnvelope(
            kind=kind,
            cycle_id=self._cycle_id,
            sequence=offset,
            payload=payload,
        )
        with self._lock:
            self._next_offset = max(self._next_offset, offset + 1)
            subs = list(self._subscribers.values())
        for sub in subs:
            sub.publish(envelope)

    def drain(self) -> None:
        """Ledger teardown hook. Closes every subscriber so HTTP handlers
        exit their stream loops cleanly."""
        self.close_all_subscribers()

    # ---- HTTP-side subscription API ----

    def subscribe(self, *, queue_size: int = _DEFAULT_QUEUE_SIZE) -> EventStreamSubscriber:
        """Create a new subscriber. The captured ``start_offset`` is the
        ``sequence`` the handler stamps on its initial ``stream_snapshot``
        frame; live tail then arrives at offsets > start_offset."""
        with self._lock:
            sub_id = next(self._sub_ids)
            start_offset = self._next_offset
            sub = EventStreamSubscriber(
                sub_id,
                start_offset,
                on_close=self._unsubscribe,
                queue_size=queue_size,
            )
            self._subscribers[sub_id] = sub
        return sub

    def _unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def close_all_subscribers(self) -> None:
        with self._lock:
            subs = list(self._subscribers.values())
            self._subscribers.clear()
        for sub in subs:
            sub.close()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ---- Snapshot reads ----

    def snapshot_payload(self) -> dict[str, Any]:
        """Read the session's ``dashboard.json`` for the leading
        ``stream_snapshot`` frame's payload.

        Returns a ``warming_up`` shape when the file isn't there yet (fresh
        campaign before the first persist). Includes ``snapshot_at_offset``
        so clients know the high-water mark of records reflected in this
        snapshot.
        """
        # dashboard.json lives in the session-family root cycle dir, NOT
        # the per-cycle dir. Resolving the session root from cycle_id is
        # the caller's job — this method walks up from the cycle dir to
        # find any sibling root with a dashboard.
        root_dashboard = self._resolve_session_dashboard_path()
        body: dict[str, Any]
        if root_dashboard is not None and root_dashboard.is_file():
            try:
                body = json.loads(root_dashboard.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning(
                    "dashboard.json malformed at %s; emitting warming_up snapshot",
                    root_dashboard,
                )
                body = {"warming_up": True, "reason": "dashboard_unreadable"}
        else:
            body = {"warming_up": True}
        body["snapshot_at_offset"] = self._next_offset
        return body

    def _resolve_session_dashboard_path(self) -> Path | None:
        """The session root carries ``dashboard.json``. Per ``root_cycle_id``
        convention, the root is found by stripping ``_fork_*`` / ``_sweep_*``
        / ``_diag_*`` suffixes from ``cycle_id`` and resolving the sibling.
        We import lazily to dodge a circular dependency."""
        from promptpotter.infrastructure.store.paths import root_cycle_id

        root_id = root_cycle_id(self._cycle_id)
        root_dir = self._cycle_dir.parent / root_id
        return root_dir / "dashboard.json"
