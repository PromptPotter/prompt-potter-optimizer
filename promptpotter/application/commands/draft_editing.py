from __future__ import annotations

from promptpotter.application.commands.dispatcher import (
    Applier,
    Dispatched,
    DispatchedToDraft,
    Replaying,
    replays,
)
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    kept_draft_wire,
    read_draft_wire,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.domain.command_kinds import (
    CommandKind,
    DiscardDraftPayload,
    EditDraftCampaignPayload,
)
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
        return await kept_draft_wire(stores, apply_draft_patch(draft, plan), plan.served)

    return Applier(_apply)


@replays(CommandKind.EDIT_DRAFT_CAMPAIGN)
async def edited(replaying: Replaying[EditDraftCampaignPayload]) -> DraftCampaignWire:
    """The draft as it reads now: its key names this patch, so the first act applied it."""
    return await reread_draft_wire(replaying.stores, replaying.payload.draft_id)


async def discard_draft(dispatched: Dispatched[DiscardDraftPayload]) -> Applier[object]:
    stores = dispatched.dispatcher.stores
    draft_id = owned_draft(stores, dispatched.payload.draft_id).draft_id

    async def _apply() -> None:
        stores.drafts.discard(stores.drafts.hold(draft_id))

    return Applier.silent(_apply)
