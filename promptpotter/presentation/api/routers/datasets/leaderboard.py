from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request, Response

from promptpotter.application.scoring.cells import measurement_log, open_cell
from promptpotter.domain.cells import Cell, CellsResponse, HeatmapScope
from promptpotter.domain.results import HardSampleOrder
from promptpotter.domain.scoring import SampleStatus
from promptpotter.presentation.api.deps import StoresDep, decode_descend
from promptpotter.presentation.api.routers.campaigns._conditional import conditional_json
from promptpotter.presentation.api.routers.datasets._router import datasets_router


@datasets_router.get("/{name}/cells", response_model=CellsResponse)
def get_dataset_cells(
    request: Request,
    name: str,
    stores: StoresDep,
    limit: int = Query(default=50, ge=1, le=1000, description="Samples per page."),
    max_unmeasured: int | None = Query(
        default=None,
        ge=0,
        le=1000,
        description="Cap on unmeasured samples kept in the ranking; None = no trim.",
    ),
    scope: Annotated[
        HeatmapScope,
        Query(
            description="dataset=cross-campaign; campaign=the cycle holding the campaign's "
            "line (needs campaign_id); cycle=one cycle (needs both ids).",
        ),
    ] = "dataset",
    campaign_id: str | None = Query(
        default=None, description="Required when scope is campaign or cycle."
    ),
    cycle_id: str | None = Query(default=None, description="Required when scope=cycle."),
    descend: str | None = Query(
        default=None,
        description=(
            "L4 inner-cycle descent tail (`~`-joined `campaign::cycle` hops below the root, "
            "mirrors `?descend=` on the dashboard route). Present → read every scope from the "
            "inner `.inner/` sandbox; needs campaign_id + cycle_id (the root hop) to walk from."
        ),
    ),
    order: Annotated[
        HardSampleOrder | None,
        Query(
            description="Override the ranking key for this read; unset ⇒ the dataset's "
            "`CampaignConfig.hard_sample_order`. The resolved value comes back on `order`.",
        ),
    ] = None,
    candidate_id: str | None = Query(
        default=None,
        description="Keep only this individual's cells (`CellCandidate.candidate_id`). A "
        "campaign's candidates carry one; a dataset-scope column does not, so there it keeps "
        "nothing.",
    ),
    round: int | None = Query(
        default=None,
        description="Keep only this round's cells. A dataset-scope column carries no round, so "
        "there it keeps nothing.",
    ),
    status: Annotated[
        SampleStatus | None, Query(description="Keep only cells with this mark.")
    ] = None,
    at: int | None = Query(
        default=None,
        ge=0,
        description="Replay scope=cycle to this line of the cycle's own ledger — the dashboard "
        "route's `at`: the candidates named and the cells walked by then. Refused on any other "
        "scope, which no single ledger offset addresses.",
    ),
) -> Response:
    """The measurement log; under a filter, ``samples`` and ``candidates`` shrink to the ones holding a kept cell."""
    log = measurement_log(
        stores,
        name,
        scope=scope,
        campaign_id=campaign_id,
        cycle_id=cycle_id,
        descend=decode_descend(descend),
        limit=limit,
        max_unmeasured=max_unmeasured,
        order=order,
        candidate_id=candidate_id,
        round=round,
        status=status,
        at=at,
    )
    return conditional_json(request, log)


@datasets_router.get("/{name}/cells/{answer}", response_model=Cell)
def get_dataset_cell(name: str, answer: str, stores: StoresDep) -> Cell:
    """One answer opened: its row assembled into a trace."""
    return open_cell(stores, name, answer)
