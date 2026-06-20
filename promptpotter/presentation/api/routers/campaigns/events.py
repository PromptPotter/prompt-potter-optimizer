"""Profile A — Server-Sent Events highway for a per-cycle ledger stream.

Single endpoint: ``GET /campaigns/{c}/cycles/{cy}/events:subscribe``.

The handler tails the cycle's on-disk ledger (``.runtime/ledger.jsonl``) — the
append-only source of truth — so the stream works regardless of which process
runs the campaign (API server, CLI, spawned runner). On attach it emits one
``stream_snapshot`` envelope carrying the cycle's ``dashboard.json`` content +
``snapshot_at_offset``; then it polls the ledger file and fans out every newly
appended record as live tail, interspersing 15 s SSE-comment heartbeats while
idle.

Wire shape (closed set): every frame is a
:class:`~promptpotter.domain.projection_envelope.ProjectionEnvelope`
serialized with ``model_dump_json``. The certified contract — including the
colon-suffix URL convention, snapshot-then-tail semantics, sequence gaps, and
proxy-buffering headers — lives in ``docs/developer/event-stream.md``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Request
from fastapi.responses import StreamingResponse

from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
from promptpotter.presentation.api.deps import StoreDep
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError

__all__ = ["stream_cycle_events"]


logger = logging.getLogger(__name__)


_SSE_HEARTBEAT_INTERVAL_S = 15.0
# How often the ledger file is polled for new records. Sub-second so the chat
# tracks a live run closely; the read is incremental (seek past what's already
# streamed), so the cost is bounded by new data, not ledger size.
_SSE_POLL_INTERVAL_S = 0.5
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


async def _stream(request: Request, tail: CycleLedgerTail) -> AsyncIterator[bytes]:
    """Snapshot frame, then poll the ledger file for live tail.

    File reads run via ``asyncio.to_thread`` so the event loop never blocks on
    disk I/O. Exits cleanly when the client disconnects.
    """
    yield _format_frame(await asyncio.to_thread(tail.snapshot_frame))

    idle_s = 0.0
    while True:
        if await request.is_disconnected():
            return
        frames = await asyncio.to_thread(tail.read_new)
        if frames:
            idle_s = 0.0
            for envelope in frames:
                yield _format_frame(envelope)
        else:
            idle_s += _SSE_POLL_INTERVAL_S
            if idle_s >= _SSE_HEARTBEAT_INTERVAL_S:
                idle_s = 0.0
                yield _heartbeat_frame()
        await asyncio.sleep(_SSE_POLL_INTERVAL_S)


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

    Returns ``404`` only when the cycle directory does not exist (unknown
    campaign/cycle). For any real cycle — running, paused, or finished — frames
    arrive in this order:

    1. One ``stream_snapshot`` frame — payload is the cycle's current
       ``dashboard.json`` body + ``snapshot_at_offset`` indicating the ledger
       high-water mark reflected in the snapshot.
    2. Live tail — every record appended to ``.runtime/ledger.jsonl`` after the
       snapshot is fanned out as a ``ProjectionEnvelope`` with ``kind`` = the
       record's ``record_type`` and ``sequence`` = the record's ledger offset.
    3. Heartbeat comment line every 15 s while idle.

    See ``docs/developer/event-stream.md`` for the certified Profile A contract.
    """
    cycle_dir = Path(store.campaigns.cycle_dir(campaign_id, cycle_id))
    if not cycle_dir.exists():
        raise NotFoundError(f"Unknown cycle {campaign_id}/{cycle_id}.")
    tail = CycleLedgerTail(cycle_dir, cycle_id)
    return StreamingResponse(
        _stream(request, tail),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
