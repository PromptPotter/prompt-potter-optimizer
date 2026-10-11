from __future__ import annotations

from typing import Literal

from fastapi import Query

from promptpotter.application import cycle_files
from promptpotter.application.cycle_files import FileContentResponse, FilesResponse
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/files",
    response_model=FilesResponse,
)
def list_cycle_files(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
) -> FilesResponse:
    """The recursive file tree of the cycle directory plus the campaign-level artifacts, at any depth through ``descend``."""
    return cycle_files.list_cycle_files(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/file",
    response_model=FileContentResponse,
)
def get_cycle_file(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    scope: Literal["cycle", "campaign"] = Query(..., description="cycle | campaign"),
    path: str = Query(..., description="Relative path under the chosen scope root"),
    descend: str | None = Query(None),
) -> FileContentResponse:
    """The contents of one file under the cycle or campaign scope; with ``descend``, the inner cycle's, never the outer root's."""
    return cycle_files.read_cycle_file(
        stores,
        (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend)),
        scope=scope,
        file=path,
    )
