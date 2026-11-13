from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier
from promptpotter.application.datasets.draft_build import DraftCampaignWire, kept_draft_wire
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
from promptpotter.application.pipeline_resolve import draft_pipeline, draft_served
from promptpotter.domain.checkin_draft import OriginResolution
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import ConflictError, PotterError, ServiceUnavailableError

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import DispatchedToDraft
    from promptpotter.domain.command_kinds import ResolveOriginPayload

logger = logging.getLogger(__name__)

# The refusal of a turn no draft owes: a door that sent an empty answer reads it as "no turn".
RESOLVER_NOT_OWED = "resolver_not_owed"


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
    # Decided here for every door, before anything is filed: only the operator's re-run buys a
    # turn the draft does not owe.
    if not (dispatched.payload.rerun or draft.resolver_owed()):
        raise ConflictError(
            "No check-in turn is owed: a turn already set this draft up, or its prompts are "
            "authored. Re-run the check-in to buy one.",
            code=RESOLVER_NOT_OWED,
        )
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
        wire = await kept_draft_wire(stores, result.draft, served, resolution=result.resolution)
        return ResolveOriginResponse(resolution=result.resolution, draft=wire)

    # Its end keeps the turn's answer: a deduped retry re-spends no LLM call, after a mint too.
    return Applier(_apply)
