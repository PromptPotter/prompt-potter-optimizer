from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Request, Response

from promptpotter.application.hierarchy import hierarchy_version
from promptpotter.application.hierarchy_subtree import subtree_version
from promptpotter.application.read_params import (
    HierarchyParams,
    HierarchyStepsParams,
    HierarchySubtreeParams,
)
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.domain.hierarchy import HierarchyEntry, HierarchyResponse, HierarchySteps
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json

hierarchy_router = APIRouter()


@hierarchy_router.get(
    "/hierarchy", name=ReadKind.HIERARCHY, response_model=HierarchyResponse, tags=["Hierarchy"]
)
def get_hierarchy(
    request: Request,
    caller: CallerDep,
    read: Annotated[HierarchyParams, read_input(HierarchyParams)],
) -> Response:
    return conditional_json(
        request,
        (hierarchy_version(caller.stores, inside=read.inside), read),
        lambda: read.answer(caller),
    )


# Declared before the subtree route, whose `{address:path}` would otherwise take the `/@` too.
@hierarchy_router.get(
    "/hierarchy/{address:path}/@",
    name=ReadKind.HIERARCHY_STEPS,
    response_model=HierarchySteps,
    tags=["Hierarchy"],
)
def get_hierarchy_steps(
    request: Request,
    caller: CallerDep,
    read: Annotated[HierarchyStepsParams, read_input(HierarchyStepsParams)],
) -> Response:
    steps = read.opened(caller.stores)
    return conditional_json(request, steps.validator, steps.window)


@hierarchy_router.get(
    "/hierarchy/{address:path}",
    name=ReadKind.HIERARCHY_SUBTREE,
    response_model=HierarchyEntry,
    tags=["Hierarchy"],
)
def get_hierarchy_subtree(
    request: Request,
    caller: CallerDep,
    read: Annotated[HierarchySubtreeParams, read_input(HierarchySubtreeParams)],
) -> Response:
    return conditional_json(
        request,
        (subtree_version(caller.stores, read.address), read),
        lambda: read.answer(caller),
    )
