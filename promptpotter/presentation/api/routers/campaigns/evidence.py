"""``/evidence``, not ``/campaigns/evidence``: that literal must out-order ``{campaign_id}``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Request, Response

from promptpotter.application.evidence.read import Evidence
from promptpotter.application.head_to_head_price import HeadToHeadPrice
from promptpotter.application.read_params import EvidenceParams, HeadToHeadPriceParams
from promptpotter.domain.command_kinds import ReadKind
from promptpotter.presentation.api.deps import CallerDep, read_input
from promptpotter.presentation.api.routers.campaigns._conditional import hashed_json
from promptpotter.presentation.api.routers.campaigns._router import campaigns_router


# No capability gate and no L4 gate: tenant-scoped, and an id naming nothing is simply absent.
@campaigns_router.get("/evidence", name=ReadKind.EVIDENCE, response_model=Evidence)
def get_evidence(
    request: Request, caller: CallerDep, read: Annotated[EvidenceParams, read_input(EvidenceParams)]
) -> Response:
    return hashed_json(request, read.answer(caller), stamp="generated_at")


@campaigns_router.post(
    "/head-to-head/price", name=ReadKind.HEAD_TO_HEAD_PRICE, response_model=HeadToHeadPrice
)
async def price_planned_head_to_head(
    caller: CallerDep, read: Annotated[HeadToHeadPriceParams, read_input(HeadToHeadPriceParams)]
) -> HeadToHeadPrice:
    return await read.answer(caller)
