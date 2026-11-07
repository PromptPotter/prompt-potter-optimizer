from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    draft_wire,
    read_draft_wire,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.origin_readiness import (
    load_resolution,
    save_checkin_draft,
)
from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
from promptpotter.application.pipeline_resolve import draft_pipeline, draft_served
from promptpotter.domain.checkin_draft import OriginResolution
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import PotterError, ServiceUnavailableError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToDraft
    from promptpotter.domain.command_kinds import ResolveOriginPayload

logger = logging.getLogger(__name__)


class ResolveOriginResponse(StrictModel):
    """One resolver turn's outcome: the checklist state it left and the post-apply draft."""

    resolution: OriginResolution
    draft: DraftCampaignWire


async def resolve_origin(
    dispatched: DispatchedToDraft[ResolveOriginPayload],
) -> Applier[ResolveOriginResponse]:
    stores = dispatched.dispatcher.stores
    draft_id = dispatched.payload.draft_id
    draft = owned_draft(stores, draft_id)
    # Before the paid call: the turn's prompt slots are the chain's declared prompt fields.
    served = await draft_served(stores, draft)
    pipeline = draft_pipeline(stores, draft, served)

    async def _apply() -> ResolveOriginResponse:
        try:
            result = await resolve_origin_turn(stores=stores, draft=draft, pipeline=pipeline)
        except PotterError:
            raise
        except Exception as exc:
            # Logged here: the 503 carries no traceback.
            logger.exception("resolve-origin turn failed for draft %s", draft_id)
            raise ServiceUnavailableError(
                f"origin resolver turn failed: {exc}", code="resolver_failed"
            ) from exc
        # A turn writes no field the pipeline reads, so the declaration read before it holds.
        wire = draft_wire(stores, result.draft, served)
        save_checkin_draft(stores, result.draft, resolution=result.resolution)
        return ResolveOriginResponse(resolution=result.resolution, draft=wire)

    async def _on_replay(_: object) -> ResolveOriginResponse:
        # The draft's `resolution.json` is the live turn's block: a deduped retry re-spends no LLM call.
        replayed = owned_draft(stores, draft_id)
        return ResolveOriginResponse(
            resolution=load_resolution(stores, replayed),
            draft=await read_draft_wire(stores, replayed),
        )

    return Applier(_apply, replay=_on_replay)
