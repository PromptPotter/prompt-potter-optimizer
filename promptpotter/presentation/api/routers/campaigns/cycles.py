from __future__ import annotations

from fastapi import Query, Request, Response

from promptpotter.application.cycle_reads import round_audit, served_round, view_cycle
from promptpotter.application.mask.record import parse_lens, parse_sample_ids
from promptpotter.application.mask.tree_lens import CourseNode, lensed_tree
from promptpotter.application.served_dashboard import (
    ServedDashboard,
    WarmingDashboard,
    served_dashboard,
)
from promptpotter.domain.cycle_paths import CycleHop
from promptpotter.domain.results import RoundResult
from promptpotter.domain.round_audit import RoundAudit
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import (
    client_has_etag,
    client_seen_at_or_after,
    http_date,
    model_json,
    weak_etag,
)
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router
from promptpotter.shared.errors import BadRequestError, NotFoundError


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
    """Live telemetry for the viewed cycle: its own ``dashboard.json``.

    The path ids address the root cycle; ``descend`` walks ``.inner/`` sandboxes one ``campaign::cycle`` hop at a time.
    Honors ``If-Modified-Since`` with a 304; a cycle still at check-in answers ``warming_up`` at 200, an absent one 404.
    ``at`` is an offset in the LEAF cycle's own ledger, the address a ray item carries: the state as of that line.
    """
    cycle = view_cycle(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
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
    response_model=RoundResult,
)
def get_cycle_round(
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    round_num: int,
    descend: str | None = Query(None),
    at: int | None = Query(None, ge=0),
) -> RoundResult:
    """One closed round, whole: its last close and the rows it names, each carrying its mark as ``status``.

    A fork answers for the rounds it inherited; a round that does not stand closed answers 404.
    ``at`` is the dashboard route's: the round as it stood at that line of the cycle's own ledger.
    """
    cycle = view_cycle(
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    closed = served_round(cycle.stores, cycle.hop, round_num, cycle.moment(at))
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
        stores, (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    )
    return round_audit(cycle, round_num)


@campaigns_router.get(
    "/campaigns/{campaign_id}/cycles/{cycle_id}/tree",
    response_model=CourseNode,
)
def get_lineage_tree(
    request: Request,
    stores: StoresDep,
    campaign_id: str,
    cycle_id: str,
    descend: str | None = Query(None),
    lens: str | None = Query(None),
    samples: str | None = Query(None),
    at: int | None = Query(None, ge=0),
    at_cycle: str | None = Query(None),
) -> Response:
    """The lineage tree rooted at this cycle, its nodes alternating ``course -> candidate -> course`` at every depth.

    ``at`` is a line of the ledger of ``at_cycle`` (a whole cycle path in the ``descend`` grammar, root hop included; absent, the tree's root): a course minted later is not in the tree, and run-state stays live.
    ``lens`` is ``score:<formula>`` (rows re-graded under another ``per_cell`` composite), ``dials:<term=weight,…>`` (the same as weights) or ``abort:<variant>`` (``<gate>_off`` or ``all_off``).
    ``samples`` is a comma-separated sample-id mask; with neither, the tree is the raw read.
    """
    path = (CycleHop(campaign_id=campaign_id, cycle_id=cycle_id), *decode_descend(descend))
    root = view_cycle(stores, path)
    mtime_ns = root.tree_changed_ns
    etag = weak_etag(mtime_ns, lens, samples, at, at_cycle)
    headers = {"ETag": etag}
    if mtime_ns is not None and client_has_etag(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    try:
        selected = parse_lens(lens, allow_abort=True) if lens else None
        sample_ids = parse_sample_ids(samples)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    replayed = view_cycle(stores, decode_descend(at_cycle)) if at_cycle else root
    return model_json(
        lensed_tree(stores, path, selected, sample_ids, replayed.moment(at)), headers=headers
    )
