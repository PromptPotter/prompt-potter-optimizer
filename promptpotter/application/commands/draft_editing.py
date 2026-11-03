from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.commands.dispatcher import Applier, CommandCall, CommandDispatcher
from promptpotter.application.datasets.draft_build import (
    DraftCampaignWire,
    draft_wire,
    read_draft_wire,
)
from promptpotter.application.datasets.draft_campaign import owned_draft
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.origin_readiness import (
    origin_delta,
    origin_projection,
    save_checkin_draft,
)
from promptpotter.domain.run_records import NoEffect, OriginEditedEffect

if TYPE_CHECKING:
    from promptpotter.application.commands.dispatcher import Dispatched, DispatchedToDraft
    from promptpotter.domain.command_kinds import DiscardDraftPayload, EditDraftCampaignPayload
    from promptpotter.infrastructure.store.stores import Stores


async def reread_draft_wire(stores: Stores, draft_id: str) -> DraftCampaignWire:
    return await read_draft_wire(stores, owned_draft(stores, draft_id))


def origin_effect(
    stores: Stores, draft_id: str, before: dict[str, Any]
) -> OriginEditedEffect | NoEffect:
    """Recorded on the ack because the command payload states only what was ASKED for."""
    moved = origin_delta(before, origin_projection(owned_draft(stores, draft_id)))
    return OriginEditedEffect(moved=moved) if moved else NoEffect()


async def edit_draft_campaign(
    dispatched: DispatchedToDraft[EditDraftCampaignPayload],
) -> Applier[DraftCampaignWire]:
    stores = dispatched.dispatcher.stores
    draft_id = dispatched.payload.draft_id
    draft = owned_draft(stores, draft_id)
    plan = await plan_draft_patch(stores, draft, dispatched.payload.patch)

    async def _apply() -> DraftCampaignWire:
        updated = apply_draft_patch(draft, plan)
        save_checkin_draft(stores, updated)
        return draft_wire(stores, updated, plan.served)

    before = origin_projection(draft)
    return Applier(
        _apply,
        replay=lambda: reread_draft_wire(stores, draft_id),
        effect_fn=lambda: origin_effect(stores, draft_id, before),
        claim=dispatched.claim,
    )


async def discard_draft(dispatched: Dispatched[DiscardDraftPayload]) -> Applier[object]:
    stores = dispatched.dispatcher.stores
    draft_id = owned_draft(stores, dispatched.payload.draft_id).draft_id

    async def _apply() -> None:
        stores.drafts.discard(stores.drafts.hold(draft_id))

    return Applier.silent(_apply)


async def dispatch_draft_patch(
    stores: Stores, call: CommandCall[EditDraftCampaignPayload]
) -> DraftCampaignWire:
    outcome = await CommandDispatcher(stores).dispatch(call)
    return cast(DraftCampaignWire, outcome.result)
