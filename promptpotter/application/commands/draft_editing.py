from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from promptpotter.application.commands.dispatcher import Applier, CommandCall, CommandDispatcher
from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.draft_campaign import load_checkin_draft
from promptpotter.application.datasets.draft_patch import apply_draft_patch, plan_draft_patch
from promptpotter.application.datasets.origin_readiness import (
    origin_delta,
    origin_projection,
    save_checkin_draft,
)
from promptpotter.shared.errors import NotFoundError

if TYPE_CHECKING:
    from promptpotter.application.commands.payloads import EditDraftCampaignPayload
    from promptpotter.application.datasets.draft_campaign import DraftCampaign
    from promptpotter.infrastructure.store.stores import Stores

__all__ = [
    "dispatch_draft_patch",
    "edit_draft_campaign",
    "origin_effect",
    "reread_draft",
    "reread_draft_wire",
]


def reread_draft(stores: Stores, draft_id: str) -> DraftCampaign:
    draft = load_checkin_draft(stores, draft_id)
    if draft is None:
        raise NotFoundError(f"draft {draft_id!r} not found.", code="command_target_not_found")
    return draft


def reread_draft_wire(stores: Stores, draft_id: str) -> DraftCampaignWire:
    return draft_wire(reread_draft(stores, draft_id), stores.base_dir)


def origin_effect(stores: Stores, draft_id: str, before: dict[str, Any]) -> dict[str, Any]:
    """Recorded on the ack because the command payload states only what was ASKED for."""
    return origin_delta(before, origin_projection(reread_draft(stores, draft_id)))


def edit_draft_campaign(
    dispatcher: CommandDispatcher, payload: EditDraftCampaignPayload
) -> Applier[DraftCampaignWire]:
    stores = dispatcher.stores
    draft_id = payload.draft_id
    draft = reread_draft(stores, draft_id)
    plan = plan_draft_patch(stores, draft, payload.patch)

    def _apply() -> DraftCampaignWire:
        updated = apply_draft_patch(draft, plan)
        save_checkin_draft(stores, updated)
        return draft_wire(updated, stores.base_dir)

    before = origin_projection(draft)
    return Applier(
        _apply,
        replay=lambda: reread_draft_wire(stores, draft_id),
        effect_fn=lambda: origin_effect(stores, draft_id, before),
    )


async def dispatch_draft_patch(
    stores: Stores, call: CommandCall[EditDraftCampaignPayload]
) -> DraftCampaignWire:
    outcome = await CommandDispatcher(stores).dispatch_checkin_command(call)
    return cast(DraftCampaignWire, outcome.result)
