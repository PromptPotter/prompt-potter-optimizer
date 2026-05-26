"""Profile A — Server-Sent Events highway for a per-cycle ledger stream.

Single endpoint: ``GET /campaigns/{c}/cycles/{cy}/events:subscribe``.

The handler looks up the cycle's active :class:`EventStreamView` in the
process-wide registry; idle cycles 404. On attach the handler emits one
``stream_snapshot`` envelope carrying the session's ``dashboard.json``
content + ``snapshot_at_offset``; then drains the subscriber's queue as
live tail, intersp 15 s SSE-comment heartbeats during idle.

Wire shape (closed set): every frame is a
:class:`~promptpotter.domain.projection_envelope.ProjectionEnvelope`
serialized with ``model_dump_json``. The certified contract — including
the colon-suffix URL convention, snapshot-then-tail semantics, sequence
gaps, and proxy-buffering headers — lives in
``docs/developer/event-stream.md``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.infrastructure.projections import (
    EventStreamSubscriber,
    EventStreamView,
    get_event_stream,
)
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router

__all__ = ["stream_cycle_events"]


logger = logging.getLogger(__name__)


_SSE_HEARTBEAT_INTERVAL_S = 15.0
# Headers that defeat proxy buffering. Critical for nginx/Cloudflare/etc.
# without these the stream sits in a buffer until 4 KB accumulates.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Content-Type": "text/event-stream",
}


def _format_frame(envelope: ProjectionEnvelope) -> bytes:
    """SSE-format a single envelope: ``data: <json>\\n\\n``."""
    return b"data: " + envelope.model_dump_json().encode("utf-8") + b"\n\n"


def _heartbeat_frame() -> bytes:
    """SSE comment line — proxies treat it as keepalive, clients ignore it."""
    return b": keepalive\n\n"


async def _stream(
    request: Request,
    view: EventStreamView,
    subscriber: EventStreamSubscriber,
) -> AsyncIterator[bytes]:
    """Bridge ``EventStreamSubscriber`` to SSE bytes.

    Snapshot frame first, then live tail. Exits cleanly when the client
    disconnects or the subscriber closes (e.g., on ``drain_all``).
    """
    import asyncio

    subscriber.attach_loop(asyncio.get_running_loop())

    # Leading snapshot frame (security box 14 — snapshot-then-tail).
    snapshot = ProjectionEnvelope(
        kind="stream_snapshot",
        cycle_id=view.cycle_id,
        sequence=subscriber.start_offset,
        payload=view.snapshot_payload(),
    )
    yield _format_frame(snapshot)

    try:
        async for envelope in subscriber.stream(heartbeat_interval_s=_SSE_HEARTBEAT_INTERVAL_S):
            if await request.is_disconnected():
                return
            if envelope is None:
                # Idle tick — heartbeat (security box 15).
                yield _heartbeat_frame()
                continue
            yield _format_frame(envelope)
    finally:
        subscriber.close()


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe",
    response_class=StreamingResponse,
    tags=["Stream"],
)
async def stream_cycle_events(
    request: Request,
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
) -> StreamingResponse:
    """Subscribe to a cycle's live ledger via SSE.

    Returns ``404`` when the cycle isn't actively running (no
    in-process ``EventStreamView`` registered). Once attached, frames
    arrive in this order:

    1. One ``stream_snapshot`` frame — payload is the session's current
       ``dashboard.json`` body + ``snapshot_at_offset`` indicating the
       ledger high-water mark reflected in the snapshot.
    2. Live tail — every subsequent ``CycleRecord`` is fanned out as a
       ``ProjectionEnvelope`` with ``kind`` = the record's ``record_type``
       and ``sequence`` = the record's ledger offset.
    3. Heartbeat comment line every 15 s during idle.

    See ``docs/developer/event-stream.md`` for the certified Profile A
    contract.
    """
    del store  # IdentityDep enforces tenant scope; cycle id is the rest of trust.
    view = get_event_stream(campaign_id, cycle_id)
    if view is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active stream for {campaign_id}/{cycle_id} — the cycle "
                "is not currently running. Start a run before subscribing."
            ),
        )
    subscriber = view.subscribe()
    return StreamingResponse(
        _stream(request, view, subscriber),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
