from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.commands.dispatcher import Applier, CommandCall, CommandDispatcher
from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.origin_readiness import (
    origin_delta,
    origin_projection,
    save_checkin_draft,
)

if TYPE_CHECKING:
    from promptpotter.domain.command_kinds import DiscardDraftPayload, EditDraftCampaignPayload
    from promptpotter.infrastructure.store.draft_store import DraftClaim
    from promptpotter.infrastructure.store.stores import Stores


def reread_draft_wire(stores: Stores, draft_id: str) -> DraftCampaignWire:
    return draft_wire(stores, owned_draft(stores, draft_id))


def origin_effect(stores: Stores, draft_id: str, before: dict[str, Any]) -> dict[str, Any]:
    """Recorded on the ack because the command payload states only what was ASKED for."""
    return origin_delta(before, origin_projection(owned_draft(stores, draft_id)))


def edit_draft_campaign(
    dispatcher: CommandDispatcher, payload: EditDraftCampaignPayload, claim: DraftClaim
) -> Applier[DraftCampaignWire]:
    stores = dispatcher.stores
    draft_id = payload.draft_id
    draft = owned_draft(stores, draft_id)
    plan = plan_draft_patch(stores, draft, payload.patch)

    def _apply() -> DraftCampaignWire:
        updated = apply_draft_patch(draft, plan)
        save_checkin_draft(stores, updated)
        return draft_wire(stores, updated)

    before = origin_projection(draft)
    return Applier(
        _apply,
        replay=lambda: reread_draft_wire(stores, draft_id),
        effect_fn=lambda: origin_effect(stores, draft_id, before),
        claim=claim,
    )


def discard_draft(dispatcher: CommandDispatcher, payload: DiscardDraftPayload) -> Applier[object]:
    stores = dispatcher.stores
    draft_id = owned_draft(stores, payload.draft_id).draft_id
    return Applier.silent(lambda: stores.drafts.discard(draft_id))


async def dispatch_draft_patch(
    stores: Stores, call: CommandCall[EditDraftCampaignPayload]
) -> DraftCampaignWire:
    outcome = await CommandDispatcher(stores).dispatch(call)
    return cast(DraftCampaignWire, outcome.result)
