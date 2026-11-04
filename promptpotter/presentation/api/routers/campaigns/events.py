from __future__ import annotations

from typing import Annotated

from sse_starlette import EventSourceResponse

from promptpotter.application.read_params import CycleEventsParams
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.domain.projection_envelope import ProjectionEnvelope
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/events:subscribe",
    name=ReadKind.CYCLE_EVENTS,
    response_class=EventSourceResponse,
    response_model=ProjectionEnvelope,
    tags=["Stream"],
)
async def stream_cycle_events(
    caller: CallerDep, read: Annotated[CycleEventsParams, read_input(CycleEventsParams)]
) -> EventSourceResponse:
    # LF is the contract (the default is CRLF); X-Accel-Buffering is the header sse-starlette omits.
    return EventSourceResponse(read.answer(caller), sep="\n", headers={"X-Accel-Buffering": "no"})
