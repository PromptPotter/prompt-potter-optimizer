from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from promptpotter.application.commands.dispatcher import Applier, CommandCall, CommandDispatcher
from promptpotter.application.commands.draft_editing import origin_effect, reread_draft
from promptpotter.application.datasets.draft_build import DraftCampaignWire, draft_wire
from promptpotter.application.datasets.origin_readiness import (
    OriginResolution,
    load_resolution,
    origin_projection,
)
from promptpotter.application.datasets.origin_resolve import resolve_origin_turn
from promptpotter.domain.strict_model import StrictModel
from promptpotter.shared.errors import PotterError, ServiceUnavailableError

if TYPE_CHECKING:
    from promptpotter.application.commands.payloads import ResolveOriginPayload
    from promptpotter.infrastructure.store.stores import Stores

logger = logging.getLogger(__name__)

__all__ = ["ResolveOriginResponse", "dispatch_origin_resolution", "resolve_origin"]


class ResolveOriginResponse(StrictModel):
    """One resolver turn's outcome: the checklist state it left and the post-apply draft."""

    resolution: OriginResolution
    draft: DraftCampaignWire


def resolve_origin(
    dispatcher: CommandDispatcher, payload: ResolveOriginPayload
) -> Applier[ResolveOriginResponse]:
    stores = dispatcher.stores
    draft_id, message = payload.draft_id, payload.message
    draft = reread_draft(stores, draft_id)

    async def _apply() -> ResolveOriginResponse:
        try:
            result = await resolve_origin_turn(stores=stores, draft=draft, message=message)
        except PotterError:
            raise
        except Exception as exc:
            # Logged here: the 503 carries no traceback.
            logger.exception("resolve-origin turn failed for draft %s", draft_id)
            raise ServiceUnavailableError(
                f"origin resolver turn failed: {exc}", code="resolver_failed"
            ) from exc
        return ResolveOriginResponse(
            resolution=result.resolution, draft=draft_wire(result.draft, stores.base_dir)
        )

    def _on_replay() -> ResolveOriginResponse:
        # `cache.json::resolution` is the live turn's block: a deduped retry re-spends no LLM call.
        replayed = reread_draft(stores, draft_id)
        return ResolveOriginResponse(
            resolution=load_resolution(stores, replayed),
            draft=draft_wire(replayed, stores.base_dir),
        )

    before = origin_projection(draft)
    return Applier(
        _apply,
        replay=_on_replay,
        effect_fn=lambda: origin_effect(stores, draft_id, before),
    )


async def dispatch_origin_resolution(
    stores: Stores, call: CommandCall[ResolveOriginPayload]
) -> ResolveOriginResponse:
    """Calling ``resolve_origin_turn`` bare records no turn AND re-spends the call a replay serves."""
    outcome = await CommandDispatcher(stores).dispatch_checkin_command(call)
    return cast(ResolveOriginResponse, outcome.result)
