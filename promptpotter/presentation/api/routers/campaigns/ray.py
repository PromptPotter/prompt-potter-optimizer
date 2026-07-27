"""The time-ray route — a shell like the ``tree`` route beside it: resolve, walk the
family once, decide the conditional GET, build the window. The merge rules live in
``store/family_ray_views.py``; the family walk is ``lineage_views.iter_family_courses``,
shared with the tree so "who belongs to this campaign" has exactly one answer."""

from __future__ import annotations

from fastapi import Query, Request, Response
from fastapi.responses import JSONResponse

from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.infrastructure.store.family_ray_views import (
    DEFAULT_RAY_LIMIT,
    MAX_RAY_LIMIT,
    RayResponse,
    build_family_ray,
    decode_ray_cursor,
    ray_validator_parts,
)
from promptpotter.infrastructure.store.layout import cycle_dir_for
from promptpotter.infrastructure.store.lineage_views import iter_family_courses
from promptpotter.presentation.api.deps import StoreDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_has_etag,
    weak_etag,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/ray",
    response_model=RayResponse,
)
def get_family_ray(
    request: Request,
    store: StoreDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    limit: int = Query(DEFAULT_RAY_LIMIT, ge=1, le=MAX_RAY_LIMIT),
    before: str | None = Query(None),
) -> Response:
    """The absolute linear sequence of what happened, rooted at this cycle.

    One merged order across the course, its forks, and its inner runs — the only surface
    that answers "what happened, in order, and is it still happening". It is also the
    replay endpoint: the SSE tail always seeks to EOF, so history has no other home.

    Windowed newest-first, delivered oldest-first. ``before`` is an opaque cursor from a
    prior response's ``cursor_prev``; absent = the head window. Consecutive windows page
    back without overlap or hole; revalidate any window with ``If-None-Match``.
    """
    try:
        cursor = decode_ray_cursor(before)
    except ValueError as exc:
        # A mangled cursor is a 400, never a tolerated default: silently falling back to
        # the head window would hand the operator a chronology with a hole in it and no
        # sign. Decoded before the conditional, so a malformed cursor can never 304.
        raise BadRequestError(str(exc)) from exc

    path = (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    # ONE family walk (it also owns the path resolution), whichever way the conditional
    # goes — the expensive half is reading the ledgers, which is what the 304 skips.
    courses = iter_family_courses(store, path)
    leaf = courses[0].path[-1]
    if not cycle_dir_for(courses[0].store.base_dir, leaf.campaign_id, leaf.cycle_id).is_dir():
        raise NotFoundError(f"Cycle '{leaf.campaign_id}/{leaf.cycle_id}' not found")

    etag = weak_etag(*ray_validator_parts(courses, limit=limit, before=before))
    headers = {"ETag": etag}
    if client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    body = build_family_ray(courses, limit=limit, before=cursor)
    return JSONResponse(body.model_dump(mode="json"), headers=headers)
