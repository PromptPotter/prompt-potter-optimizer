from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated

from fastapi import Request, Response

from promptpotter.application.cycle_reads import RoundView
from promptpotter.application.read_params import (
    CycleDashboardParams,
    CycleExportParams,
    CycleRoundAuditParams,
    CycleRoundParams,
)
from promptpotter.application.served_dashboard import ServedDashboard, WarmingDashboard
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_seen_at_or_after,
    http_date,
    model_json,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard",
    name=ReadKind.CYCLE_DASHBOARD,
    response_model=ServedDashboard | WarmingDashboard,
)
def get_cycle_dashboard(
    request: Request,
    caller: CallerDep,
    read: Annotated[CycleDashboardParams, read_input(CycleDashboardParams)],
) -> Response:
    cycle = read.viewed(caller.stores)
    headers: dict[str, str] = {}
    mtime_epoch = cycle.dashboard_changed_at
    if mtime_epoch is not None:
        headers["Last-Modified"] = http_date(mtime_epoch)
        if client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    return model_json(read.served(cycle), headers=headers)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}",
    name=ReadKind.CYCLE_ROUND,
    response_model=RoundView,
)
def get_cycle_round(
    caller: CallerDep, read: Annotated[CycleRoundParams, read_input(CycleRoundParams)]
) -> RoundView:
    return read.answer(caller)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}/audit",
    name=ReadKind.CYCLE_ROUND_AUDIT,
    response_model=RoundAudit | None,
)
def get_cycle_round_audit(
    caller: CallerDep, read: Annotated[CycleRoundAuditParams, read_input(CycleRoundAuditParams)]
) -> RoundAudit | None:
    return read.answer(caller)


_EXPORT_MEDIA = {".txt": "text/plain", ".json": "application/json", ".zip": "application/zip"}


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/export",
    name=ReadKind.CYCLE_EXPORT,
    response_class=Response,
    responses={200: {"content": {media: {} for media in _EXPORT_MEDIA.values()}}},
)
def get_cycle_export(
    caller: CallerDep, read: Annotated[CycleExportParams, read_input(CycleExportParams)]
) -> Response:
    files = read.answer(caller)
    name = files.download_name
    single = files.single
    return Response(
        content=files.zipped() if single is None else single.encode(),
        media_type=_EXPORT_MEDIA[PurePosixPath(name).suffix],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
