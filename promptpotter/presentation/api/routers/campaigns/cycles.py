from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import Query, Request, Response

from promptpotter.application.cycle_reads import (
    RoundView,
    export_cycle,
    round_audit,
    served_round,
    view_cycle,
)
from promptpotter.application.served_dashboard import (
    ServedDashboard,
    WarmingDashboard,
    served_dashboard,
)
from promptpotter.domain.cycle_paths import CycleHop, decode_cycle_path
from promptpotter.domain.export import ExportTier
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.presentation.api.deps import StoresDep
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_seen_at_or_after,
    http_date,
    model_json,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import NotFoundError


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/dashboard",
    response_model=ServedDashboard | WarmingDashboard,
)
def get_cycle_dashboard(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    at: int | None = Query(None, ge=0),
) -> Response:
    """Live telemetry for the viewed cycle: the fold of its own ledger chain.

    The path ids address the root cycle; ``descend`` walks ``.inner/`` sandboxes one ``campaign::cycle`` hop at a time.
    Honors ``If-Modified-Since`` with a 304; a cycle with no declared wiring answers ``warming_up`` at 200, an absent one 404.
    ``at`` is an offset in the LEAF cycle's own ledger, the address a ray item carries: the state as of that line.
    """
    cycle = view_cycle(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_cycle_path(descend))
    )
    headers: dict[str, str] = {}
    mtime_epoch = cycle.dashboard_changed_at
    if mtime_epoch is not None:
        headers["Last-Modified"] = http_date(mtime_epoch)
        if client_seen_at_or_after(request.headers.get("if-modified-since"), mtime_epoch):
            return Response(status_code=304, headers=headers)
    return model_json(served_dashboard(cycle.stores, cycle.hop, at=at), headers=headers)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}",
    response_model=RoundView,
)
def get_cycle_round(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    round_num: int,
    descend: str | None = Query(None),
    at: int | None = Query(None, ge=0),
) -> RoundView:
    """One closed round: its last close's arms and verdict, and the rows it names, each carrying its mark as ``status``.

    A fork answers for the rounds it inherited; a round that does not stand closed answers 404.
    ``at`` is the dashboard route's: the round as it stood at that line of the cycle's own ledger.
    """
    cycle = view_cycle(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_cycle_path(descend))
    )
    closed = served_round(cycle.stores, cycle.hop, round_num, moment=cycle.moment(at))
    if closed is None:
        raise NotFoundError(
            f"Round {round_num} of '{cycle.hop.campaign_id}/{cycle.hop.cycle_id}' is not closed"
        )
    return closed


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/rounds/{round_num}/audit",
    response_model=RoundAudit | None,
)
def get_cycle_round_audit(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    round_num: int,
    descend: str | None = Query(None),
) -> RoundAudit | None:
    """The round's audit twin: the per-node LLM I/O, which the round beside it does not carry.

    A fork answers for the rounds it inherited; ``null`` where no twin is on disk.
    """
    cycle = view_cycle(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_cycle_path(descend))
    )
    return round_audit(cycle, round_num)


_EXPORT_MEDIA = {".txt": "text/plain", ".json": "application/json", ".zip": "application/zip"}


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/export",
    response_class=Response,
    responses={200: {"content": {media: {} for media in _EXPORT_MEDIA.values()}}},
)
def get_cycle_export(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    tier: ExportTier,
    descend: str | None = Query(None),
) -> Response:
    """One reading of the cycle, chosen by what its reader does next — the CLI's `export --tier`.

    ``chat`` and ``report`` are text to paste, ``deploy`` is ``export.json``; ``notebook`` and ``package`` are several files, served as one zip.
    ``deploy`` and ``package`` answer 409 until the cycle has written ``export.json``.
    """
    files = export_cycle(
        stores,
        (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_cycle_path(descend)),
        tier,
    )
    name = files.download_name
    single = files.single
    return Response(
        content=files.zipped() if single is None else single.encode(),
        media_type=_EXPORT_MEDIA[PurePosixPath(name).suffix],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
