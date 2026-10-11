from __future__ import annotations

from fastapi import Query, Request, Response

from promptpotter.application.cycle_reads import (
    DEFAULT_RAY_LIMIT,
    MAX_RAY_LIMIT,
    RayResponse,
    open_family_ray,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_has_etag,
    model_json,
    weak_etag,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/ray",
    response_model=RayResponse,
)
def get_family_ray(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    limit: int = Query(DEFAULT_RAY_LIMIT, ge=1, le=MAX_RAY_LIMIT),
    before: str | None = Query(None),
) -> Response:
    """One merged, ordered sequence of what happened across this cycle, its forks and its inner runs.

    It is the replay endpoint: the SSE tail always seeks to EOF, so history has no other home.
    Windowed newest-first, delivered oldest-first; ``before`` is the opaque ``cursor_prev`` of a prior response (absent = the head window, malformed = 400, never a 304).
    Consecutive windows page back without overlap or hole; revalidate any window with ``If-None-Match``.
    """
    path = (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    ray = open_family_ray(stores, path, limit=limit, before=before)
    etag = weak_etag(*ray.validator)
    headers = {"ETag": etag}
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return model_json(ray.window(), headers=headers)
