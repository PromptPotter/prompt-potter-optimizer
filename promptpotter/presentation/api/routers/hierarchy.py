from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from promptpotter.application.cycle_reads import open_family_ray
from promptpotter.application.hierarchy import hierarchy_version, list_hierarchy
from promptpotter.application.hierarchy_subtree import (
    hierarchy_subtree,
    subtree_moment,
    subtree_version,
)
from promptpotter.application.mask.record import parse_lens, parse_sample_ids
from promptpotter.domain.campaign import LifecycleFilter
from promptpotter.domain.cycle_paths import decode_cycle_path
from promptpotter.domain.hierarchy import (
    DEFAULT_STEP_LIMIT,
    MAX_STEP_LIMIT,
    HierarchyEntry,
    HierarchyResponse,
    HierarchySteps,
)
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json

hierarchy_router = APIRouter()


@hierarchy_router.get("/hierarchy", response_model=HierarchyResponse, tags=["Hierarchy"])
def get_hierarchy(
    request: Request,
    stores: StoresDep,
    descend: str | None = Query(None),
    dataset: str | None = Query(default=None, description="Filter to one dataset"),
    lifecycle: Annotated[
        LifecycleFilter,
        Query(
            description="'active' (default), 'archived' or 'deleted' — the visibility intent; "
            "or 'all'",
        ),
    ] = "active",
    campaign: str | None = Query(default=None, description="Filter to one campaign id"),
    attached: bool = Query(False, description="Keep only the courses a producer holds"),
    depth: int = Query(1, ge=0, description="0 serves campaigns alone, 1 their courses"),
) -> Response:
    """Every campaign in one store the caller owns, as hierarchy entries, each with its courses.

    ``descend`` enters an inner store exactly as every per-cycle read does.
    """
    inside = decode_cycle_path(descend)
    return conditional_json(
        request,
        (hierarchy_version(stores, inside=inside), dataset, lifecycle, campaign, attached, depth),
        lambda: list_hierarchy(
            stores,
            inside=inside,
            dataset=dataset,
            lifecycle=lifecycle,
            campaign_id=campaign,
            attached_only=attached,
            depth=depth,
        ),
    )


# Declared before the subtree route, whose `{address:path}` would otherwise take the `/@` too.
@hierarchy_router.get(
    "/hierarchy/{address:path}/@", response_model=HierarchySteps, tags=["Hierarchy"]
)
def get_hierarchy_steps(
    request: Request,
    stores: StoresDep,
    address: str,
    limit: int = Query(DEFAULT_STEP_LIMIT, ge=1, le=MAX_STEP_LIMIT),
    before: str | None = Query(None),
) -> Response:
    """The course's chronology: its steps, its forks' and its inner runs', merged by time.

    The replay endpoint: the SSE tail always seeks to EOF, so history has no other home.
    Windowed newest-first, delivered oldest-first; ``before`` is the opaque ``cursor_prev`` of a prior
    response (absent = the head window, malformed = 400, never a 304). Consecutive windows page back
    without overlap or hole; revalidate any window with ``If-None-Match``.
    """
    steps = open_family_ray(stores, decode_cycle_path(address), limit=limit, before=before)
    return conditional_json(request, steps.validator, steps.window)


@hierarchy_router.get(
    "/hierarchy/{address:path}", response_model=HierarchyEntry, tags=["Hierarchy"]
)
def get_hierarchy_subtree(
    request: Request,
    stores: StoresDep,
    address: str,
    lens: str | None = Query(None),
    samples: str | None = Query(None),
    at: int | None = Query(None, ge=0),
    at_cycle: str | None = Query(None),
) -> Response:
    """The entry at ``address`` with every level under it: courses, rounds, candidates, inner campaigns.

    ``at`` is a line of the ledger of ``at_cycle`` (a whole cycle path, root hop included; absent, the
    campaign's root): a course minted later is not in the subtree, and run-state stays live.
    ``lens`` is ``score:<formula>`` (rows re-graded under another ``per_cell`` composite),
    ``dials:<term=weight,…>`` (the same as weights) or ``abort:<variant>`` (``<gate>_off`` or ``all_off``).
    ``samples`` is a comma-separated sample-id mask; with neither, the subtree is the raw read. 404 where
    no entry stands at ``address``.
    """
    masked = bool(lens or samples)
    return conditional_json(
        request,
        (subtree_version(stores, address, masked=masked), address, lens, samples, at, at_cycle),
        lambda: hierarchy_subtree(
            stores,
            address,
            parse_lens(lens, allow_abort=True) if lens else None,
            parse_sample_ids(samples),
            moment=subtree_moment(stores, address, at, decode_cycle_path(at_cycle)),
        ),
    )
