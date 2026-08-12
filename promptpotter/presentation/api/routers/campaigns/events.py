"""Profile A — the SSE highway over ``CycleLedgerTail``; ``sse-starlette`` owns framing, heartbeat and teardown. The
certified contract is ``docs/developer/event-stream.md``."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Query
from sse_starlette import EventSourceResponse

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.projections.event_stream import CycleLedgerTail
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.stores import resolve_cycle_path
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError

__all__ = ["stream_cycle_events"]


logger = logging.getLogger(__name__)


# How often the ledger file is polled for new records. Sub-second so the chat
# tracks a live run closely; the read is incremental (seek past what's already
# streamed), so the cost is bounded by new data, not ledger size.
_SSE_POLL_INTERVAL_S = 0.5


async def _stream(tail: CycleLedgerTail) -> AsyncIterator[str]:
    """Snapshot envelope, then poll the ledger for the live tail, yielding each frame as a JSON string. File reads run via
    ``asyncio.to_thread`` so the event loop never blocks on disk I/O."""
    yield (await asyncio.to_thread(tail.snapshot_frame)).model_dump_json()
    while True:
        for envelope in await asyncio.to_thread(tail.read_new):
            yield envelope.model_dump_json()
        await asyncio.sleep(_SSE_POLL_INTERVAL_S)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe",
    response_class=EventSourceResponse,
    tags=["Stream"],
)
async def stream_cycle_events(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
) -> EventSourceResponse:
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
    3. Heartbeat comment line every 15 s.

    The path ids address the top-level (root) cycle; the optional ``descend``
    query walks into the previous hop's ``.inner/<key>`` sandbox one
    ``campaign::cycle`` hop at a time, so ONE stream serves a top-level cycle or
    an L4 inner descendant (:func:`resolve_cycle_path`) — the chat feed follows
    the same viewed leaf the dashboard does. Absent/empty ``descend`` is a plain
    per-cycle subscribe, byte-identical to a top-level read.

    See ``docs/developer/event-stream.md`` for the certified Profile A contract.
    """
    leaf_store, leaf = resolve_cycle_path(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    leaf_campaign, leaf_cycle = leaf.campaign_id, leaf.cycle_id
    cycle_dir = Path(cycle_dir_for(leaf_store.base_dir, leaf))
    if not cycle_dir.exists():
        raise NotFoundError(f"Unknown cycle {leaf_campaign}/{leaf_cycle}.")
    tail = CycleLedgerTail(cycle_dir, leaf)
    # sep="\n": LF line endings (the contract's `data: <json>\n\n`; the default
    # is CRLF). X-Accel-Buffering: the one proxy-defeating header sse-starlette
    # does not set itself; Content-Type it sets, Cache-Control the no_store_on_api
    # middleware forces to no-store regardless.
    return EventSourceResponse(_stream(tail), sep="\n", headers={"X-Accel-Buffering": "no"})
