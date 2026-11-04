from __future__ import annotations

from typing import TYPE_CHECKING

from promptpotter.application.commands.dispatcher import Applier, CommandCall, CommandDispatcher
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    draft_wire,
    read_draft_wire,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.origin_readiness import save_checkin_draft

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched, DispatchedToDraft
    from promptpotter.domain.command_kinds import DiscardDraftPayload, EditDraftCampaignPayload
    from promptpotter.infrastructure.store.stores import Stores


async def reread_draft_wire(stores: Stores, draft_id: str) -> DraftCampaignWire:
    return await read_draft_wire(stores, owned_draft(stores, draft_id))


async def edit_draft_campaign(
    dispatched: DispatchedToDraft[EditDraftCampaignPayload],
) -> Applier[DraftCampaignWire]:
    stores = dispatched.dispatcher.stores
    draft_id = dispatched.payload.draft_id
    draft = owned_draft(stores, draft_id)
    plan = await plan_draft_patch(stores, draft, dispatched.payload.patch)

    async def _apply() -> DraftCampaignWire:
        updated = apply_draft_patch(draft, plan)
        wire = draft_wire(stores, updated, plan.served)
        save_checkin_draft(stores, updated)
        return wire

    return Applier(_apply, replay=lambda _: reread_draft_wire(stores, draft_id))


async def discard_draft(dispatched: Dispatched[DiscardDraftPayload]) -> Applier[object]:
    stores = dispatched.dispatcher.stores
    draft_id = owned_draft(stores, dispatched.payload.draft_id).draft_id

    async def _apply() -> None:
        stores.drafts.discard(stores.drafts.hold(draft_id))

    return Applier.silent(_apply)


async def dispatch_draft_patch(
    stores: Stores, call: CommandCall[EditDraftCampaignPayload]
) -> DraftCampaignWire:
    outcome = await CommandDispatcher(stores).answer(edit_draft_campaign, call)
    return outcome.result
