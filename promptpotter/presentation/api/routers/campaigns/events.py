from __future__ import annotations

from fastapi import Query
from sse_starlette import EventSourceResponse

from promptpotter.application.cycle_reads import cycle_event_frames, view_cycle
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router

__all__ = ["stream_cycle_events"]


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
    """Subscribe to a cycle's live ledger over SSE, at any depth through ``descend``.

    404 only for an unknown cycle; a running, paused or finished one answers one ``stream_snapshot`` frame (the served dashboard, ``sequence`` = the offset the tail picks up at), then every later record as a ``ProjectionEnvelope`` (``kind`` = its ``record_type``, ``sequence`` = its ledger offset), with a heartbeat comment every 15 s.
    """
    cycle = view_cycle(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    # LF is the contract (the default is CRLF); X-Accel-Buffering is the header sse-starlette omits.
    return EventSourceResponse(
        cycle_event_frames(cycle), sep="\n", headers={"X-Accel-Buffering": "no"}
    )
